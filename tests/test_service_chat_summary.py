from scholarmotion.api.service import (
    _verification_chat_summary,
    _verification_failure_chat_summary,
)
from scholarmotion.schemas import Timeline, TimelineSegment, VerificationReport
from scholarmotion.services.orchestration import BuildResult, ProducedScene


def test_completion_summary_exposes_duration_subtitles_and_quality_layers():
    report = VerificationReport(
        scene_id="S01",
        passed=True,
        score=1.0,
        layers={"render": True, "layout": True, "math": True},
    )
    scene = ProducedScene(
        spec=None,  # The completion summary only needs the verification report.
        code_path="code.py",
        audio=None,
        render_path="scene.mp4",
        report=report,
    )
    result = BuildResult(
        project_id="project",
        scenes=[scene],
        video_path="final.mp4",
        timeline=Timeline(
            video_id="video",
            duration_seconds=120,
            scenes=[TimelineSegment(scene_id="S01", start=0, end=120, render_version=1)],
        ),
        srt_path="subtitles.srt",
        vtt_path="subtitles.vtt",
        events=[],
    )

    summary, metadata = _verification_chat_summary(result)

    assert "120.0 seconds" in summary
    assert "SRT/VTT subtitles" in summary
    assert "No verification issues" in summary
    assert metadata == {
        "lesson_review": {},
        "scene_count": 1,
        "duration_seconds": 120,
        "subtitle_formats": ["srt", "vtt"],
        "passed_layers": ["layout", "math", "render"],
        "issue_count": 0,
        "degraded_scenes": [],
        "missing_scenes": [],
    }


def test_failure_summary_is_visible_in_the_chat_and_never_claims_publication():
    summary, metadata = _verification_failure_chat_summary(RuntimeError("S02 failed layout QA"))

    assert "not published" in summary
    assert "S02 failed layout QA" in summary
    assert metadata["published"] is False
