"""FFmpeg assembly of rendered scenes and narration into the final video."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssemblyResult:
    success: bool
    output_path: str
    log: str = ""
    ordered_inputs: tuple[str, ...] = ()


def resolve_ffmpeg(binary: str = "ffmpeg") -> str | None:
    """Resolve a system FFmpeg binary, then fall back to the bundled executable."""
    executable = shutil.which(binary)
    if executable:
        return executable
    if binary != "ffmpeg":
        return None
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    bundled = imageio_ffmpeg.get_ffmpeg_exe()
    return bundled if Path(bundled).is_file() else None


class FFmpegAssembler:
    def __init__(self, ffmpeg_binary: str = "ffmpeg", *, mock_missing: bool = True):
        self.ffmpeg_binary = ffmpeg_binary
        self.mock_missing = mock_missing

    def _probe_duration(self, path: str) -> float | None:
        probe = shutil.which("ffprobe")
        if probe:
            completed = subprocess.run(
                [
                    probe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                return float(completed.stdout.strip()) if completed.returncode == 0 else None
            except ValueError:
                return None

        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg:
            return None
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr)
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def probe_duration(self, path: str) -> float | None:
        """Read media duration for timeline and mux alignment callers."""
        return self._probe_duration(path)

    def retime_wav(
        self, input_path: str | Path, target_seconds: float, output_path: str | Path
    ) -> bool:
        """Preserve narration pitch while fitting an audio track to its scene budget.

        ``atempo`` accepts only factors in [0.5, 2], so decompose longer
        adjustments into legal stages.  This is used for modest TTS-rate
        variation; callers retain the original track if it cannot be safely
        normalized.
        """
        source, output = Path(input_path), Path(output_path)
        actual = self.probe_duration(str(source))
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg or actual is None or actual <= 0 or target_seconds <= 0:
            return False
        tempo = actual / target_seconds
        if not 0.65 <= tempo <= 1.35:
            return False
        factors: list[float] = []
        remaining = tempo
        while remaining > 2:
            factors.append(2.0)
            remaining /= 2
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        factors.append(remaining)
        output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-filter:a",
                ",".join(f"atempo={factor:.8f}" for factor in factors),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0 and output.is_file()

    def finalize_ending(
        self, input_path: str | Path, *, hold_seconds: float = 1.5, fade_seconds: float = 1.0
    ) -> bool:
        """Hold the closing frame after the last word, then fade picture and sound.

        Scene renders stop exactly when narration stops, so an unpadded lesson
        cuts off on the final syllable. A short settled hold reads as an ending.
        """
        source = Path(input_path)
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        duration = self.probe_duration(str(source)) if ffmpeg else None
        if not ffmpeg or duration is None or duration <= 0:
            return False
        total = duration + hold_seconds
        fade_start = max(0.0, total - fade_seconds)
        output = source.with_name(f"{source.stem}.ending{source.suffix}")
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                (
                    f"[0:v]tpad=stop_mode=clone:stop_duration={hold_seconds:.3f},"
                    f"fade=t=out:st={fade_start:.3f}:d={fade_seconds:.3f}[v];"
                    f"[0:a]apad=pad_dur={hold_seconds:.3f},"
                    f"afade=t=out:st={fade_start:.3f}:d={fade_seconds:.3f}[a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{total:.3f}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(output),
            ],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            return False
        output.replace(source)
        return True

    def extract_review_excerpt(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        start_seconds: float,
        duration_seconds: float,
        output_video: str | Path,
        output_audio: str | Path,
    ) -> bool:
        """Extract a small, time-aligned AV excerpt for a multimodal reviewer."""
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg or start_seconds < 0 or duration_seconds <= 0:
            return False
        video_output, audio_output = Path(output_video), Path(output_audio)
        video_output.parent.mkdir(parents=True, exist_ok=True)
        audio_output.parent.mkdir(parents=True, exist_ok=True)
        video = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-an",
                "-vf",
                "fps=10,scale='min(854,iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                str(video_output),
            ],
            capture_output=True,
            check=False,
        )
        if video.returncode != 0 or not video_output.is_file():
            return False
        audio = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(audio_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_output),
            ],
            capture_output=True,
            check=False,
        )
        return audio.returncode == 0 and audio_output.is_file()

    def extract_review_keyframes(
        self,
        video_path: str | Path,
        timestamps_seconds: list[float],
        output_dir: str | Path,
    ) -> list[Path]:
        """Extract stills from the same short clip sent to the AV reviewer."""
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg:
            return []
        duration = self.probe_duration(str(video_path))
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        extracted = []
        for index, timestamp in enumerate(timestamps_seconds):
            if timestamp < 0 or (duration is not None and timestamp >= duration):
                continue
            path = destination / f"frame-{index + 1:02d}.png"
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(854,iw)':-2",
                    str(path),
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0 and path.is_file():
                extracted.append(path)
        return extracted

    def still_scene(
        self,
        output_path: str | Path,
        duration_seconds: float,
        *,
        size: str = "854x480",
        fps: int = 15,
        color: str = "0x0B1020",
    ) -> bool:
        """A plain background clip of the given length, needing only FFmpeg.

        The last-resort visual for a scene whose every Manim render failed: the
        narration still plays over it, so the lesson keeps that explanation.
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg:
            if not self.mock_missing:
                return False
            output.write_bytes(b"SCHOLARMOTION_MOCK_VIDEO")
            return True
        command = [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r={fps}:d={max(0.5, duration_seconds):.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        return completed.returncode == 0 and output.exists()

    def _mux_command(self, ffmpeg: str, video: str, audio: str, output: Path) -> list[str]:
        video_seconds, audio_seconds = self._probe_duration(video), self._probe_duration(audio)
        command = [ffmpeg, "-y", "-i", video, "-i", audio]
        if (
            video_seconds is not None
            and audio_seconds is not None
            and abs(video_seconds - audio_seconds) > 0.05
        ):
            if video_seconds < audio_seconds:
                padding = audio_seconds - video_seconds
                command += [
                    "-filter_complex",
                    f"[0:v]tpad=stop_mode=clone:stop_duration={padding:.3f}[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "1:a",
                    "-t",
                    f"{audio_seconds:.3f}",
                ]
            else:
                padding = video_seconds - audio_seconds
                command += [
                    "-filter_complex",
                    f"[1:a]apad=pad_dur={padding:.3f}[a]",
                    "-map",
                    "0:v",
                    "-map",
                    "[a]",
                    "-t",
                    f"{video_seconds:.3f}",
                ]
            command += [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        else:
            command += [
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
            ]
        return [*command, str(output)]

    def assemble(
        self,
        videos: list[str],
        output_path: str | Path,
        *,
        audio_paths: list[str] | None = None,
        audio_normalization: bool = False,
    ) -> AssemblyResult:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = resolve_ffmpeg(self.ffmpeg_binary)
        if not ffmpeg:
            if self.mock_missing:
                output.write_bytes(b"SCHOLARMOTION_MOCK_VIDEO")
                return AssemblyResult(
                    True, str(output), "ffmpeg unavailable; mock output", tuple(videos)
                )
            return AssemblyResult(False, str(output), "ffmpeg unavailable", tuple(videos))
        if not videos:
            return AssemblyResult(False, str(output), "no scene videos")
        if audio_paths is not None and len(audio_paths) != len(videos):
            return AssemblyResult(
                False,
                str(output),
                f"scene video/audio count mismatch: {len(videos)} videos, {len(audio_paths)} audio tracks",
                tuple(videos),
            )

        if len(videos) == 1:
            audio = audio_paths[0] if audio_paths else None
            command = self._segment_command(
                ffmpeg, videos[0], audio, output, audio_normalization=audio_normalization
            )
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            return AssemblyResult(
                completed.returncode == 0 and output.exists(),
                str(output),
                completed.stdout + completed.stderr,
                tuple(videos),
            )

        logs: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="scholarmotion-assembly-", dir=output.parent
        ) as temp:
            temp_dir = Path(temp)
            clips: list[Path] = []
            for index, video in enumerate(videos):
                clip = temp_dir / f"scene-{index:04}.mp4"
                audio = audio_paths[index] if audio_paths else None
                command = self._segment_command(
                    ffmpeg, video, audio, clip, audio_normalization=audio_normalization
                )
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                logs.append(completed.stdout + completed.stderr)
                if completed.returncode != 0 or not clip.exists():
                    return AssemblyResult(
                        False, str(output), "\n".join(logs)[-4000:], tuple(videos)
                    )
                clips.append(clip)

            manifest = temp_dir / "scenes.ffconcat"
            manifest.write_text(
                "".join(_concat_file_entry(clip) for clip in clips), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(manifest),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            logs.append(completed.stdout + completed.stderr)
            return AssemblyResult(
                completed.returncode == 0 and output.exists(),
                str(output),
                "\n".join(logs)[-4000:],
                tuple(videos),
            )

    def _segment_command(
        self,
        ffmpeg: str,
        video: str,
        audio: str | None,
        output: Path,
        *,
        audio_normalization: bool,
    ) -> list[str]:
        if audio:
            return self._mux_command(ffmpeg, video, audio, output)
        if audio_normalization:
            return [
                ffmpeg,
                "-y",
                "-i",
                video,
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(output),
            ]
        return [ffmpeg, "-y", "-i", video, "-c", "copy", str(output)]


def _concat_file_entry(path: Path) -> str:
    escaped = str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'")
    return f"file '{escaped}'\n"
