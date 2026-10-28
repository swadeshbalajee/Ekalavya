"""Visual beats after the last narration cue are skipped and reported, never cut off."""

from __future__ import annotations

import pytest

from scholarmotion.manim_runtime.components import CueClock
from scholarmotion.manim_runtime.markers import CUE_OVERFLOW_MARKER
from scholarmotion.verification.render import verify_cue_overflow


class _Scene:
    def __init__(self):
        self.time = 0.0
        self.plays = 0

    def play(self, *_animations, run_time: float = 1.0, **_kwargs):
        self.plays += 1
        self.time += run_time

    def wait(self, duration: float):
        self.time += duration


def test_extra_beat_after_the_last_cue_is_skipped_and_reported(capsys):
    scene = _Scene()
    clock = CueClock(scene, [2.0, 3.0])
    clock.play("intro", run_time=1.0)
    clock.next_cue()
    clock.play("main", run_time=1.0)
    clock.next_cue()
    # One beat too many, like a 5-beat scene written for 4 cues.
    clock.play("extra", run_time=1.5)
    clock.play("extra highlight", run_time=1.5)
    clock.finish()
    assert scene.plays == 2
    assert scene.time == pytest.approx(5.0)
    assert clock.overflow == 2
    assert capsys.readouterr().out.count(CUE_OVERFLOW_MARKER) == 2


def test_a_scene_with_one_beat_per_cue_ends_with_the_narration(capsys):
    scene = _Scene()
    clock = CueClock(scene, [2.0, 3.0])
    clock.play("intro")
    clock.next_cue()
    clock.play("main")
    clock.finish()
    assert scene.time == pytest.approx(5.0)
    assert clock.overflow == 0
    assert CUE_OVERFLOW_MARKER not in capsys.readouterr().out


def test_overflow_in_the_render_log_asks_for_a_repair_with_the_cue_count():
    log = f"Rendering...\n{CUE_OVERFLOW_MARKER} 1 4\n{CUE_OVERFLOW_MARKER} 2 4\nDone"
    [issue] = verify_cue_overflow("S01", log, 4)
    assert issue.category == "video.beat_after_narration"
    assert issue.severity == "high"
    assert "2 time(s)" in issue.description
    assert "exactly 4 beats" in issue.suggested_repair


def test_a_clean_render_log_has_no_overflow_issue():
    assert verify_cue_overflow("S01", "Rendering...\nDone", 4) == []
