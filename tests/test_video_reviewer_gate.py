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
