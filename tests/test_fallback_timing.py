from types import SimpleNamespace

from scholarmotion.services.orchestration import (
    _fallback_video_findings,
    _scheduled_fallback_matches_audio,
)


def test_scheduled_fallback_matches_measured_sentence_cues():
    code = """
SCHOLARMOTION_DETERMINISTIC_FALLBACK = True
class Scene:
    def construct(self):
        self.wait(2.0)
        self.wait(3.0)
"""
    cues = [SimpleNamespace(start=0.0, end=2.0), SimpleNamespace(start=2.0, end=5.0)]

    assert _scheduled_fallback_matches_audio(code, cues)


def test_scheduled_fallback_rejects_wait_that_drifts_from_audio():
    code = """
SCHOLARMOTION_DETERMINISTIC_FALLBACK = True
class Scene:
    def construct(self):
        self.wait(2.5)
        self.wait(2.5)
"""
    cues = [SimpleNamespace(start=0.0, end=2.0), SimpleNamespace(start=2.0, end=5.0)]

    assert not _scheduled_fallback_matches_audio(code, cues)


def test_scheduled_fallback_accepts_animation_and_waits_with_matching_total():
    code = """
SCHOLARMOTION_DETERMINISTIC_FALLBACK = True
class Scene:
    def construct(self):
        self.play(Indicate(flow), run_time=0.8)
        self.wait(1.2)
        self.play(Indicate(flow), run_time=2.0)
        self.wait(1.0)
"""
    cues = [SimpleNamespace(start=0.0, end=2.0), SimpleNamespace(start=2.0, end=5.0)]

    assert _scheduled_fallback_matches_audio(code, cues)


def test_scheduled_fallback_accepts_cue_aligned_visual_animation():
    code = """
SCHOLARMOTION_DETERMINISTIC_FALLBACK = True
class Scene:
    def construct(self):
        self.play(Indicate(flow_arrow), run_time=2.0)
        self.play(Indicate(flow_arrow), run_time=3.0)
"""
    cues = [SimpleNamespace(start=0.0, end=2.0), SimpleNamespace(start=2.0, end=5.0)]

    assert _scheduled_fallback_matches_audio(code, cues)


def test_timing_review_is_not_suppressed_for_model_generated_code():
    code = """
class Scene:
    def construct(self):
        self.wait(5.0)
"""
    cues = [SimpleNamespace(start=0.0, end=5.0)]

    assert not _scheduled_fallback_matches_audio(code, cues)


def test_exact_fallback_timing_uses_measured_schedule_but_keeps_visual_findings():
    findings = [
        {"category": "video.audio_video_sync"},
        {"category": "video.stale_visuals"},
        {"category": "video.semantic_visual_mismatch"},
        {"category": "video.overlap"},
    ]

    assert _fallback_video_findings(findings) == findings[2:]
