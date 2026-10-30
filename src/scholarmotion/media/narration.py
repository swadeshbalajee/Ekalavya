"""Split one continuous lesson narration into per-scene tracks.

Synthesizing every scene separately lets a neural voice drift in pitch,
energy and pace between scenes, which learners hear as "a different
narrator". Speaking the full lesson in one request keeps one performance;
this module cuts that track at the paragraph pauses between scenes.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2

    @property
    def duration(self) -> float:
        return self.end - self.start


def _read_mono(path: Path) -> tuple[np.ndarray, int, wave._wave_params]:
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        frames = source.readframes(params.nframes)
    if params.sampwidth != 2:
        raise ValueError("lesson narration must be 16-bit PCM")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if params.nchannels > 1:
        samples = samples.reshape(-1, params.nchannels).mean(axis=1)
    return samples, params.framerate, params


def find_silences(
    samples: np.ndarray, rate: int, *, window_seconds: float = 0.02, min_seconds: float = 0.25
) -> list[Silence]:
    """Return pauses whose loudness stays well below the speech level."""
    window = max(1, int(rate * window_seconds))
    count = len(samples) // window
    if count == 0:
        return []
    energy = np.sqrt(np.mean(samples[: count * window].reshape(count, window) ** 2, axis=1))
    speech_level = float(np.percentile(energy, 90)) or 1.0
    quiet = energy < speech_level * 0.06
    silences: list[Silence] = []
    start: int | None = None
    for index, is_quiet in enumerate([*quiet, False]):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            begin, end = start * window_seconds, index * window_seconds
            if end - begin >= min_seconds:
                silences.append(Silence(begin, end))
            start = None
    return silences


def plan_cuts(
    word_counts: list[int], duration: float, silences: list[Silence]
) -> list[float] | None:
    """Choose one pause per scene boundary, or None when the split is unreliable.

    Expected boundaries come from each scene's share of spoken words. Among the
    pauses near that estimate, the longest wins: a paragraph break between
    scenes is longer than an ordinary sentence pause.
    """
    if len(word_counts) < 2:
        return []
    total = sum(word_counts)
    if total <= 0 or duration <= 0:
        return None
    cuts: list[float] = []
    cumulative = 0
    previous = 0.0
    for index, words in enumerate(word_counts[:-1]):
        cumulative += words
        expected = duration * cumulative / total
        scene_expected = duration * words / total
        window = max(2.0, scene_expected * 0.3)
        candidates = [
            silence
            for silence in silences
            if abs(silence.middle - expected) <= window and silence.middle > previous + 0.5
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda s: (round(s.duration, 1), -abs(s.middle - expected)))
        cuts.append(best.middle)
        previous = best.middle
    # Reject a split that gives any scene an implausible share of the audio.
    bounds = [0.0, *cuts, duration]
    for words, start, end in zip(word_counts, bounds[:-1], bounds[1:], strict=True):
        expected = duration * words / total
        if not 0.6 * expected <= end - start <= 1.6 * expected:
            return None
    return cuts


def split_lesson_wav(
    lesson_path: Path,
    word_counts: list[int],
    output_paths: list[Path],
    cuts: list[float] | None = None,
) -> list[float] | None:
    """Write one WAV per scene and return their durations, or None if unsafe.

    ``cuts`` are measured scene boundaries (from forced alignment); without
    them the boundaries are guessed from word counts and detected silences.
    """
    samples, rate, params = _read_mono(lesson_path)
    duration = len(samples) / rate
    if cuts is None:
        cuts = plan_cuts(word_counts, duration, find_silences(samples, rate))
    if cuts is None:
        return None
    with wave.open(str(lesson_path), "rb") as source:
        raw = source.readframes(params.nframes)
    frame_bytes = params.sampwidth * params.nchannels
    boundaries = [0, *(int(cut * rate) for cut in cuts), params.nframes]
    durations = []
    for path, start, end in zip(output_paths, boundaries[:-1], boundaries[1:], strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as target:
            target.setparams(params)
            target.writeframes(raw[start * frame_bytes : end * frame_bytes])
        durations.append((end - start) / rate)
    return durations
