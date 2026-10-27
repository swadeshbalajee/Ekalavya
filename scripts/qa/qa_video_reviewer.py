"""Compare video-capable reviewer models on scenes with known ground truth.

Usage: python scripts/qa/qa_video_reviewer.py MODEL [MODEL ...]
Each case is a rendered scene (video+narration) a human has already judged.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from scholarmotion.config import get_settings
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.services.orchestration import scene_review_prompt

SCENES = Path("projects/b363a8eb-9333-42bb-9a94-252c5bf62a42/scenes/S02")
CASES = [
    # (label, render version, expected verdict)
    ("v1: red label written over the equation", "v1", False),
    ("v4: clean matrix, highlight and equation", "v4", True),
]


async def review(model: str, label: str, version: str, expected: bool) -> dict:
    settings = get_settings()
    provider = OpenAICompatibleProvider(
        api_key=settings.openrouter_api_key,
        model=model,
        base_url="https://openrouter.ai/api/v1",
        supports_images=True,
        reasoning_effort="low",
    )
    assembler = FFmpegAssembler(mock_missing=False)
    qa_path = SCENES / "render" / version / "video-qa.mp4"
    code = sorted((SCENES / "code").glob("*.py"))[int(version[1:]) - 1].read_text(encoding="utf-8")
    spec = json.loads((SCENES / "spec" / "v1.json").read_text(encoding="utf-8"))
    audio = json.loads((SCENES / "audio" / "v1.json").read_text(encoding="utf-8"))
    duration = assembler.probe_duration(str(qa_path))
    with tempfile.TemporaryDirectory() as tmp:
        clip, wav = Path(tmp) / "c.mp4", Path(tmp) / "c.wav"
        assembler.extract_review_excerpt(qa_path, SCENES / "audio" / "v1.wav", 0, duration, clip, wav)
        frames = assembler.extract_review_keyframes(
            clip, [duration * i / 7 for i in range(7)], Path(tmp) / "f"
        )
        prompt = scene_review_prompt(
            objective=spec["learning_objective"],
            tags=spec["tags"],
            narration=spec["narration"],
            cues=[(c["start"], c["end"], c["text"]) for c in audio["sentence_timings"]],
            source_code=code,
        )
        started = time.monotonic()
        try:
            result = await provider.analyze_video(
                prompt,
                clip.read_bytes(),
                wav.read_bytes(),
                keyframes=[f.read_bytes() for f in frames],
                excerpt_duration_seconds=duration,
            )
        except Exception as error:  # noqa: BLE001 - report provider failures per model
            return {"model": model, "case": label, "error": f"{type(error).__name__}: {error}"[:400]}
    passed = result.get("makes_sense") is not False and not any(
        i["severity"] in {"high", "critical"} for i in result["issues"]
    )
    return {
        "model": model,
        "case": label,
        "expected_pass": expected,
        "judged_pass": passed,
        "correct": passed == expected,
        "seconds": round(time.monotonic() - started, 1),
        "summary": result.get("summary", "")[:300],
        "issues": [f"{i['category']} [{i['severity']}]: {i['description'][:160]}" for i in result["issues"]],
    }


async def main() -> None:
    rows = [await review(model, *case) for model in sys.argv[1:] for case in CASES]
    for row in rows:
        print(json.dumps(row, indent=1, ensure_ascii=False))
    out = Path(".local/qa/e2e-2026-10-25/video-reviewer-comparison.json")
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
