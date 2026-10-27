from scholarmotion.manim_runtime.timing import scale_manim_durations
from scholarmotion.media.audio import scale_timings
from scholarmotion.media.timeline import build_timeline, media_duration
from scholarmotion.schemas import TimingCue


def test_scene_timeline_uses_the_longer_muxed_stream():
    assert media_duration(34.7, 47.8) == 47.8
    assert media_duration(53.1, 23.6) == 53.1
    assert media_duration(30.0, None) == 30.0


def test_subsequent_scene_starts_after_longer_animation():
    timeline = build_timeline(
        [
            ("S01", media_duration(34.7, 47.8), 1, "s01.mp4"),
            ("S02", media_duration(20.0, 18.0), 1, "s02.mp4"),
        ]
    )

    assert timeline.scenes[0].end == 47.8
    assert timeline.scenes[1].start == 47.8
    assert timeline.duration_seconds == 67.8


def test_animation_time_scaling_matches_audio_without_freezing_last_frame():
    code = "self.play(Write(title))\nself.wait(4)\nself.wait()"

    scaled = scale_manim_durations(code, 1.5)

    assert "run_time=1.5" in scaled
    assert "self.wait(6.0)" in scaled
    assert "self.wait(1.5)" in scaled


def test_scale_timings_keeps_subtitles_aligned_with_normalized_speech():
    scaled = scale_timings([TimingCue(text="One.", start=2, end=6)], 1.25)

    assert scaled == [TimingCue(text="One.", start=2.5, end=7.5)]
