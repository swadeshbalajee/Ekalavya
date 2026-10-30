"""Re-make real scenes with a code model setup and record how each one went.

The test set for model and prompt changes. Each scene is re-made with its
existing narration through the app's own scene pipeline (code, render, checks,
video review, repairs), so results match production. Nothing is written to the
projects or the database; output goes under --out.

Run inside the worker container:
    python scripts/eval_scenes.py --setups default
    python scripts/eval_scenes.py --setups default,z-ai/glm-5.3-flash --out /tmp/eval

A setup is "default" (the configured code model with its stronger take-over
model) or one model id (that model alone, no take-over). Per scene it records
pass/fail, code calls by model, cost, cached tokens, checklist scores and three
stills. --scenes takes label=scene-uuid pairs; it defaults to the scenes below.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from scholarmotion.api.main import database
from scholarmotion.api.service import create_pipeline
from scholarmotion.config import get_settings
from scholarmotion.observability.usage import track_usage
from scholarmotion.persistence.models import AudioArtifact, Project, Scene, SceneSpecification
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import create_code_escalation_provider, create_code_provider
from scholarmotion.schemas import SceneSpec, TTSResult

# Chosen to provoke the usual failures: highlights on paper figures, chart values
# and label overlap, dense Tamil vector diagrams, equation lists, and timed cues.
DEFAULT_SCENES = {
    "A-figure-plots": "7377a40b-cf68-49b3-92b0-96119c9939d5",
    "B-graph-values": "d78135db-275f-40dd-9c1a-c078b8fc04dc",
    "C-dense-vectors": "1a92a903-6a75-4fe8-828e-a98e52f84f42",
    "D-equations": "46e81677-c272-475a-b1a6-b390d258507e",
    "E-practice-cues": "fe9db7b1-7f8b-46a6-bc5a-73b82333032e",
}
CODE_PURPOSES = {"code", "code_repair"}


def _figures_here(spec: SceneSpec, projects: Path) -> SceneSpec:
    """Point paper figures at their file when a plan names an older folder."""
    figures = []
    for figure in spec.paper_figures:
        if figure.path and not Path(figure.path).exists():
            found = sorted(projects.glob(f"**/paper_figures/{Path(figure.path).name}"))
            if found:
                figure = figure.model_copy(update={"path": str(found[0])})
        figures.append(figure)
    return spec.model_copy(update={"paper_figures": figures})


def _stills(video: str, target: Path) -> list[str]:
    duration = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    paths = []
    for index, fraction in enumerate((0.2, 0.5, 0.85), 1):
        frame = target / f"still{index}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{float(duration or 0) * fraction:.2f}",
                "-i",
                video,
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-1",
                str(frame),
            ],
            check=False,
        )
        if frame.exists():
            paths.append(str(frame))
    return paths


async def run_scene(setup: str, label: str, scene_id: str, out: Path) -> dict:
    settings = get_settings()
    if setup != "default":
        settings = settings.model_copy(
            update={"code_model": setup, "code_fallback_models": "", "code_escalation_model": None}
        )
    work = out / setup.replace("/", "_") / label
    work.mkdir(parents=True, exist_ok=True)
    record: dict = {"setup": setup, "scene": label}
    started = time.perf_counter()
    async with database.sessions() as session:
        sid = UUID(scene_id)
        spec_row = await session.scalar(
            select(SceneSpecification).where(
                SceneSpecification.scene_id == sid, SceneSpecification.active.is_(True)
            )
        )
        audio_row = await session.scalar(
            select(AudioArtifact).where(
                AudioArtifact.scene_id == sid, AudioArtifact.active.is_(True)
            )
        )
        project = await session.get(Project, (await session.get(Scene, sid)).project_id)
        spec = _figures_here(
            SceneSpec.model_validate(spec_row.payload), settings.object_storage_root
        )
        pipeline = create_pipeline(
            settings,
            session,
            text_language=project.text_language,
            voice_language=project.voice_language,
        )
        pipeline.storage = LocalObjectStore(work / "store")
        pipeline.code_llm = create_code_provider(settings)
        pipeline.code_escalation_llm = create_code_escalation_provider(settings)
        audio = TTSResult(
            audio_path=audio_row.uri,
            duration_seconds=audio_row.duration,
            sentence_timings=audio_row.timings,
            word_timings=[],
        )
        with track_usage() as ledger:
            try:
                produced = await pipeline.produce_scene(
                    f"eval-{label}",
                    spec,
                    artifact_version=1,
                    persist_spec=False,
                    existing_audio=audio,
                    known_source_ids=set(spec.source_ids),
                    prefetched_corrections=[],
                )
                record.update(
                    passed=produced.report.passed,
                    attempts=produced.render_version,
                    issues=[
                        f"{issue.severity} {issue.category}: {issue.description[:200]}"
                        for issue in produced.report.issues
                    ],
                    render=produced.render_path,
                )
                checklists = sorted((work / "store").glob("**/verification/*-checklist.json"))
                record["checklist"] = [json.loads(path.read_text()) for path in checklists]
                if produced.render_path:
                    record["stills"] = _stills(produced.render_path, work)
            except Exception as error:  # noqa: BLE001 - a failed scene is a result
                record.update(passed=False, error=f"{type(error).__name__}: {error}"[:600])
    code = [entry for entry in ledger.entries if entry.purpose in CODE_PURPOSES]
    record.update(
        seconds=round(time.perf_counter() - started),
        code_calls=[entry.model for entry in code],
        code_cost=round(sum(entry.cost_usd or 0 for entry in code), 4),
        other_cost=round(
            sum(
                entry.cost_usd or 0
                for entry in ledger.entries
                if entry.purpose not in CODE_PURPOSES
            ),
            4,
        ),
        input_tokens=sum(entry.input_tokens for entry in code),
        cached_tokens=sum(entry.cached_tokens for entry in code),
        reasoning_tokens=sum(entry.reasoning_tokens for entry in code),
    )
    (work / "result.json").write_text(json.dumps(record, indent=2))
    print(
        f"[done] {setup:<28} {label:<16} passed={record.get('passed')} "
        f"calls={len(record['code_calls'])} code=${record['code_cost']} "
        f"other=${record['other_cost']} {record['seconds']}s {record.get('error', '')[:100]}",
        flush=True,
    )
    return record


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--setups", default="default")
    parser.add_argument("--scenes", default="")
    parser.add_argument("--out", default="/tmp/eval")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    scenes = (
        dict(pair.split("=", 1) for pair in args.scenes.split(","))
        if args.scenes
        else DEFAULT_SCENES
    )
    out = Path(args.out)
    gate = asyncio.Semaphore(args.concurrency)

    async def bounded(setup: str, label: str, scene_id: str) -> dict:
        async with gate:
            return await run_scene(setup, label, scene_id, out)

    results = await asyncio.gather(
        *(
            bounded(setup.strip(), label, scene_id)
            for setup in args.setups.split(",")
            for label, scene_id in scenes.items()
        )
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2))
    for setup in dict.fromkeys(result["setup"] for result in results):
        rows = [result for result in results if result["setup"] == setup]
        print(
            f"[summary] {setup}: {sum(bool(r.get('passed')) for r in rows)}/{len(rows)} passed, "
            f"code ${sum(r['code_cost'] for r in rows):.3f}, "
            f"other ${sum(r['other_cost'] for r in rows):.3f}, "
            f"calls {sum(len(r['code_calls']) for r in rows)}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
