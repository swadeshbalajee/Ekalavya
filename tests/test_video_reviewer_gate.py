"""The video reviewer, not static geometry, decides whether a scene passes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scholarmotion.agents.manager import Manager
from scholarmotion.manim_runtime.sandbox import RenderResult
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.schemas import SceneSpec, TimingCue, TTSResult, VisualBeat
from scholarmotion.services import ScholarMotionPipeline


def _pipeline(tmp_path, verdict: dict | None) -> ScholarMotionPipeline:
    # Two labels whose boxes overlap: the old static gate rejected this outright.
    bounds = tmp_path / "bounds.json"
    bounds.write_text(
        json.dumps(
            [
                [
                    {
                        "name": "MathTex_1",
                        "left": 0,
                        "right": 2,
                        "bottom": 0,
                        "top": 0.5,
                        "text_height": 0.5,
                        "font_size": 40,
                    },
                    {
                        "name": "MathTex_2",
                        "left": 0.5,
                        "right": 2.5,
                        "bottom": 0.1,
                        "top": 0.6,
                        "text_height": 0.5,
                        "font_size": 40,
                    },
                ]
            ]
        )
    )
    video = tmp_path / "scene.mp4"
    video.write_bytes(b"video")

    class Code:
        name = "openai_compatible"

        async def generate_code(self, prompt):
            return "from manim import *\nclass SceneS01(Scene):\n def construct(self):\n  self.wait(4)\n"

    class Speech:
        async def synthesize(self, text, path, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            return TTSResult(
                audio_path=str(path),
                duration_seconds=4,
                sentence_timings=[TimingCue(text=text, start=0, end=4)],
            )

    class Reviewer:
        calls = 0

        async def analyze_video(self, *args, **kwargs):
            Reviewer.calls += 1
            return verdict

    def assemble(_videos, output, **_):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"qa")
        return SimpleNamespace(success=True, log="")

    def excerpt(_qa, _audio, _start, _duration, clip, wav):
        clip.write_bytes(b"clip")
        wav.write_bytes(b"wav")
        return True

    pipeline = object.__new__(ScholarMotionPipeline)
    pipeline.storage = LocalObjectStore(tmp_path / "store")
    pipeline.translator = None
    pipeline.progress_callback = None
    pipeline.code_llm = Code()
    pipeline.tts = Speech()
    pipeline.voice_language = "English"
    pipeline.skills = SimpleNamespace(context_for=lambda _role: _async(""))
    pipeline.manager = Manager()
    pipeline.vision = None
    pipeline.video_verifier = Reviewer() if verdict is not None else None
    pipeline.assembler = SimpleNamespace(
        probe_duration=lambda _path: 4.0,
        assemble=assemble,
        extract_review_excerpt=excerpt,
        extract_review_keyframes=lambda *_: [],
    )
    pipeline.renderer = SimpleNamespace(
        render=lambda *_: RenderResult(True, str(video), "", "", [], 1, 0, None, str(bounds))
    )
    return pipeline


async def _async(value):
    return value


SPEC = SceneSpec(
    scene_id="S01",
    chapter_id="C01",
    title="Test",
    learning_objective="Explain a concept",
    narration="A clear explanation.",
    duration_target_seconds=4,
    visual_beats=[
        VisualBeat(beat_id="b", narration_segment="A clear explanation.", visual="Diagram")
    ],
)


@pytest.mark.asyncio
async def test_reviewer_approval_passes_despite_geometric_overlap(tmp_path):
    pipeline = _pipeline(tmp_path, {"makes_sense": True, "summary": "Clear.", "issues": []})
    result = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])
    assert result.report.passed
    assert "layout" not in result.report.layers


@pytest.mark.asyncio
async def test_reviewer_saying_it_does_not_make_sense_rejects_the_scene(tmp_path):
    pipeline = _pipeline(
        tmp_path,
        {"makes_sense": False, "summary": "Labels pile up and hide the formula.", "issues": []},
    )
    pipeline.manager.max_repair_attempts = 0
    result = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])
    # Rejected, never passed: once repairs run out it ships marked degraded with
    # the reviewer's reason, instead of failing a whole lecture.
    assert not result.report.passed
    assert any(issue.category == "video.does_not_make_sense" for issue in result.report.issues)


@pytest.mark.asyncio
async def test_geometry_still_gates_when_no_video_reviewer_exists(tmp_path):
    pipeline = _pipeline(tmp_path, None)
    pipeline.manager.max_repair_attempts = 0
    result = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])
    # Layout-only faults ship as a degraded scene instead of a passed one.
    assert not result.report.passed
    assert result.report.layers["layout"] is False


def _checklist(**failed) -> dict:
    """A reviewer answer in checklist form; ``failed`` maps item id to what was seen."""
    from scholarmotion.verification.checklist import CHECKLIST

    return {
        "summary": "A scene.",
        "issues": [],
        "checklist": [
            {
                "id": item.id,
                "passed": item.id not in failed,
                "description": failed.get(item.id, ""),
                "suggested_repair": "Move the label." if item.id in failed else "",
            }
            for item in CHECKLIST
        ],
    }


@pytest.mark.asyncio
async def test_a_clean_checklist_passes_and_a_failed_must_item_rejects(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    passing = await _pipeline(tmp_path / "a", _checklist()).produce_scene(
        "p", SPEC, prefetched_corrections=[]
    )
    assert passing.report.passed

    pipeline = _pipeline(tmp_path / "b", _checklist(text_overlap="12.9 sits on the axis"))
    pipeline.manager.max_repair_attempts = 0
    failing = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])
    assert not failing.report.passed
    overlap = next(i for i in failing.report.issues if i.category.endswith("text_overlap"))
    assert overlap.severity == "high"
    assert "12.9 sits on the axis" in overlap.description


@pytest.mark.asyncio
async def test_one_minor_checklist_miss_still_passes_as_feedback(tmp_path):
    result = await _pipeline(tmp_path, _checklist(not_crowded="busy")).produce_scene(
        "p", SPEC, prefetched_corrections=[]
    )
    assert result.report.passed
    assert [i.severity for i in result.report.issues] == ["low"]


class _CodeModel:
    """Records each request; ``fail`` makes every request raise."""

    def __init__(self, model: str, calls: list, fail: bool = False):
        self.name, self.model, self.calls, self.fail = "openai_compatible", model, calls, fail

    async def generate_code(self, prompt, *, cached_prefix=None, purpose="code", images=None):
        if self.fail:
            raise RuntimeError(f"{self.model} is down")
        self.calls.append((self.model, prompt, len(images or [])))
        return (
            "from manim import *\nclass SceneS01(Scene):\n def construct(self):\n  self.wait(4)\n"
        )


def _escalating_pipeline(tmp_path, verdicts: list[dict], calls: list, cheap_down=False):
    pipeline = _pipeline(tmp_path, verdicts[0])
    answers = iter(verdicts)

    async def review(*_args, **_kwargs):
        return next(answers)

    pipeline.video_verifier = SimpleNamespace(analyze_video=review)
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    pipeline.assembler.extract_review_keyframes = lambda *_: [frame]
    pipeline.code_llm = _CodeModel("cheap", calls, fail=cheap_down)
    pipeline.code_escalation_llm = _CodeModel("strong", calls)
    pipeline.escalate_after_failures = 2
    return pipeline


@pytest.mark.asyncio
async def test_stronger_model_fixes_then_rewrites_after_two_cheap_failures(tmp_path, monkeypatch):
    from scholarmotion.agents import code_generator

    monkeypatch.setattr(code_generator, "CODE_RETRY_DELAY_SECONDS", 0)
    bad = _checklist(text_overlap="labels on the bars")
    calls: list = []
    pipeline = _escalating_pipeline(tmp_path, [bad, bad, bad, _checklist()], calls)
    result = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])

    assert result.report.passed
    assert [model for model, _, _ in calls] == ["cheap", "cheap", "strong", "strong"]
    _, fix_prompt, fix_images = calls[2]
    # The first hand-over repairs the cheap model's code, sees the failed render,
    # and knows what the earlier attempts already tried.
    assert "FAILED SOURCE TO REPAIR" in fix_prompt
    assert "EARLIER ATTEMPTS" in fix_prompt
    assert "Attempt 1 (cheap)" in fix_prompt and "Attempt 2 (cheap)" in fix_prompt
    assert fix_images == 1
    # If that fix fails too, the stronger model starts from scratch.
    _, fresh_prompt, fresh_images = calls[3]
    assert "FAILED SOURCE TO REPAIR" not in fresh_prompt
    assert fresh_images == 0


@pytest.mark.asyncio
async def test_unavailable_code_model_hands_the_scene_to_the_stronger_one(tmp_path, monkeypatch):
    from scholarmotion.agents import code_generator

    monkeypatch.setattr(code_generator, "CODE_RETRY_DELAY_SECONDS", 0)
    calls: list = []
    pipeline = _escalating_pipeline(tmp_path, [_checklist()], calls, cheap_down=True)
    result = await pipeline.produce_scene("p", SPEC, prefetched_corrections=[])

    assert result.report.passed
    assert [model for model, _, _ in calls] == ["strong"]
