"""Regression cover for scene audio/video muxing.

A scene's animation and its narration are produced independently and rarely
match in length. Muxing them with ``-shortest`` truncated the longer stream,
which discarded most of the voiceover whenever the animation was shorter — a
7-second video against 490 seconds of narration in the case that prompted this.
"""

from __future__ import annotations

from pathlib import Path

from scholarmotion.media.ffmpeg import FFmpegAssembler, resolve_ffmpeg


class _FixedDurations(FFmpegAssembler):
    """Assembler with ffprobe stubbed out, so the command logic is testable."""

    def __init__(self, durations: dict[str, float | None]):
        super().__init__()
        self._durations = durations

    def _probe_duration(self, path: str) -> float | None:
        return self._durations.get(path)


def _command(video_seconds, audio_seconds) -> list[str]:
    assembler = _FixedDurations({"v.mp4": video_seconds, "a.wav": audio_seconds})
    return assembler._mux_command("ffmpeg", "v.mp4", "a.wav", Path("out.mp4"))


def test_default_ffmpeg_resolves_to_bundled_executable(monkeypatch):
    import shutil

    import imageio_ffmpeg

    monkeypatch.setattr(shutil, "which", lambda _binary: None)

    assert resolve_ffmpeg() == imageio_ffmpeg.get_ffmpeg_exe()


def test_ffmpeg_duration_probe_parses_input_metadata_without_ffprobe(monkeypatch):
    import shutil
    from types import SimpleNamespace

    from scholarmotion.media.ffmpeg import FFmpegAssembler

    monkeypatch.setattr(shutil, "which", lambda _binary: None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stderr="Input #0, wav, from 'voice.wav':\n  Duration: 00:00:12.345, bitrate: 100 kb/s\n"
        ),
    )

    assert FFmpegAssembler()._probe_duration("voice.wav") == 12.345


def test_assembler_muxes_each_scene_then_joins_the_whole_lesson(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scholarmotion.media.ffmpeg as media_ffmpeg

    videos = [tmp_path / "scene-1.mp4", tmp_path / "scene-2.mp4"]
    audios = [tmp_path / "scene-1.wav", tmp_path / "scene-2.wav"]
    for path in videos + audios:
        path.write_bytes(b"input")
    output = tmp_path / "lesson.mp4"
    commands = []
    concat_manifest = []
    monkeypatch.setattr(media_ffmpeg, "resolve_ffmpeg", lambda _binary: "/usr/bin/ffmpeg")
    monkeypatch.setattr(FFmpegAssembler, "_probe_duration", lambda _self, _path: 4.0)

    def run(command, **_kwargs):
        commands.append(command)
        if "concat" in command:
            manifest = Path(command[command.index("-i") + 1])
            concat_manifest.extend(manifest.read_text().splitlines())
            Path(command[-1]).write_bytes(b"lesson")
        else:
            Path(command[-1]).write_bytes(b"muxed scene")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(media_ffmpeg.subprocess, "run", run)

    result = FFmpegAssembler().assemble(
        [str(path) for path in videos],
        output,
        audio_paths=[str(path) for path in audios],
    )

    assert result.success
    assert result.ordered_inputs == tuple(str(path) for path in videos)
    assert len(commands) == 3
    assert "-i" in commands[0] and str(videos[0]) in commands[0] and str(audios[0]) in commands[0]
    assert "-i" in commands[1] and str(videos[1]) in commands[1] and str(audios[1]) in commands[1]
    assert len(concat_manifest) == 2
    assert output.read_bytes() == b"lesson"


def test_narration_longer_than_animation_is_not_truncated():
    command = _command(1.0, 61.2)
    joined = " ".join(command)
    assert "-shortest" not in joined
    assert "tpad=stop_mode=clone:stop_duration=60.200" in joined
    assert "-t 61.200" in joined


def test_animation_longer_than_narration_pads_audio_with_silence():
    command = _command(45.0, 30.0)
    joined = " ".join(command)
    assert "-shortest" not in joined
    assert "apad=pad_dur=15.000" in joined
    assert "-t 45.000" in joined


def test_matching_durations_keep_the_fast_stream_copy():
    command = _command(30.0, 30.02)
    joined = " ".join(command)
    assert "-c:v copy" in joined
    assert "tpad" not in joined


def test_unprobeable_input_falls_back_to_stream_copy():
    """Without ffprobe there is nothing to align against; do not re-encode."""
    command = _command(None, 61.2)
    joined = " ".join(command)
    assert "-c:v copy" in joined
    assert "tpad" not in joined


def test_padded_output_is_encoded_for_broad_playback():
    command = _command(1.0, 61.2)
    joined = " ".join(command)
    assert "-c:v libx264" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "-c:a aac" in joined


def test_output_audio_is_pinned_to_a_playable_rate():
    """loudnorm emits 96 kHz AAC unless the output rate is pinned.

    QuickTime and most players cannot decode 96 kHz AAC and play silence, so a
    video that measures as having audio still sounds empty to the user.
    """
    import shutil
    from unittest.mock import patch

    from scholarmotion.media.ffmpeg import FFmpegAssembler

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        raise SystemExit  # stop before ffmpeg actually runs

    assembler = FFmpegAssembler()
    with (
        patch.object(shutil, "which", return_value="/usr/bin/ffmpeg"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        try:
            assembler.assemble([__file__], "out.mp4", audio_normalization=True)
        except (SystemExit, FileNotFoundError, ValueError):
            pass

    command = captured.get("command", [])
    assert "-ar" in command and command[command.index("-ar") + 1] == "48000"
    assert "-ac" in command and command[command.index("-ac") + 1] == "2"


def test_retime_wav_uses_pitch_preserving_tempo_and_pcm_output(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scholarmotion.media.ffmpeg as media_ffmpeg

    source, output = tmp_path / "voice.wav", tmp_path / "voice.timed.wav"
    source.write_bytes(b"input")
    commands = []
    monkeypatch.setattr(media_ffmpeg, "resolve_ffmpeg", lambda _binary: "/usr/bin/ffmpeg")
    monkeypatch.setattr(FFmpegAssembler, "_probe_duration", lambda _self, _path: 30.0)

    def run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"timed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media_ffmpeg.subprocess, "run", run)

    assert FFmpegAssembler().retime_wav(source, 40, output)
    command = commands[0]
    assert "atempo=0.75000000" in command
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert command[command.index("-ar") + 1] == "16000"


def test_review_excerpt_extracts_aligned_audio_and_video(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scholarmotion.media.ffmpeg as media_ffmpeg

    monkeypatch.setattr(media_ffmpeg, "resolve_ffmpeg", lambda _binary: "/usr/bin/ffmpeg")
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"excerpt")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media_ffmpeg.subprocess, "run", run)
    output_video, output_audio = tmp_path / "clip.mp4", tmp_path / "clip.wav"

    assert FFmpegAssembler().extract_review_excerpt(
        "scene.mp4", "narration.wav", 12.5, 8, output_video, output_audio
    )
    assert len(commands) == 2
    assert commands[0][commands[0].index("-ss") + 1] == "12.500"
    assert commands[1][commands[1].index("-ss") + 1] == "12.500"
    assert commands[0][commands[0].index("-t") + 1] == "8.000"
    assert commands[1][commands[1].index("-t") + 1] == "8.000"
    assert "-an" in commands[0] and "-vn" in commands[1]
    assert output_video.read_bytes() == output_audio.read_bytes() == b"excerpt"


def test_review_keyframes_are_extracted_from_the_supplied_excerpt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import scholarmotion.media.ffmpeg as media_ffmpeg

    monkeypatch.setattr(media_ffmpeg, "resolve_ffmpeg", lambda _binary: "/usr/bin/ffmpeg")
    monkeypatch.setattr(FFmpegAssembler, "_probe_duration", lambda _self, _path: 8.0)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"png")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media_ffmpeg.subprocess, "run", run)
    frames = FFmpegAssembler().extract_review_keyframes(
        "excerpt.mp4", [0.5, 4.0, 7.5], tmp_path / "excerpt-frames"
    )

    assert len(frames) == 3
    assert [command[command.index("-ss") + 1] for command in commands] == [
        "0.500",
        "4.000",
        "7.500",
    ]
    assert all("excerpt.mp4" in command for command in commands)
    assert all(frame.read_bytes() == b"png" for frame in frames)
