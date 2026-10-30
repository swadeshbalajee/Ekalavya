"""WAV duration and sentence-timing estimates used to align visuals with speech."""

from __future__ import annotations

import re
import wave
from pathlib import Path
from itertools import pairwise

from scholarmotion.schemas import TimingCue, VisualBeat


def align_visual_beats(
    narration: str,
    beats: list[VisualBeat],
    duration: float,
    *,
    word_timings: list[TimingCue] | None = None,
) -> list[VisualBeat]:
    """Use spoken phrase positions for beat timing, with an even fallback.

    With ``word_timings`` (one measured cue per narration word) each beat
    starts when its first word is actually spoken; otherwise positions are
    word-weighted estimates. A long first explanation must not be given the
    same time as a two-word final cue merely because both are storyboard beats.
    """
    if not beats:
        return []
    text = re.sub(r"\s+", " ", narration).strip().casefold()
    word_count = max(1, len(text.split()))
    measured = word_timings if word_timings and len(word_timings) == word_count else None
    starts = []
    cursor = 0
    for beat in beats:
        phrase = re.sub(r"\s+", " ", beat.narration_segment).strip().casefold()
        position = text.find(phrase, cursor) if phrase else -1
        if position < 0:
            starts = []
            break
        index = len(text[:position].split())
        if measured is not None and index < len(measured):
            starts.append(measured[index].start)
        else:
            starts.append(duration * index / word_count)
        cursor = position + len(phrase)
    if len(starts) != len(beats) or any(b <= a for a, b in pairwise(starts)):
        starts = [duration * index / len(beats) for index in range(len(beats))]
    starts[0] = 0.0
    return [
        beat.model_copy(update={"start_seconds": start, "end_seconds": end})
        for beat, start, end in zip(beats, starts, [*starts[1:], duration], strict=True)
    ]


def wav_duration(path: str | Path) -> float:
    # wave.open treats anything but a str as an open file, so a Path must become a str.
    with wave.open(str(path), "rb") as value:
        return value.getnframes() / value.getframerate()


def estimate_sentence_timings(text: str, duration: float) -> list[TimingCue]:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()] or [
        text
    ]
    weights = [max(1, len(re.findall(r"\S+", item))) for item in sentences]
    total = sum(weights)
    cursor = 0.0
    cues = []
    for sentence, weight in zip(sentences, weights):
        end = duration if sentence == sentences[-1] else cursor + duration * weight / total
        cues.append(TimingCue(text=sentence, start=cursor, end=max(cursor + 0.001, end)))
        cursor = end
    return cues


def scale_timings(cues: list[TimingCue], scale: float) -> list[TimingCue]:
    """Map subtitle/animation cue offsets after speech-rate normalization."""
    if scale <= 0:
        raise ValueError("timing scale must be positive")
    return [
        cue.model_copy(update={"start": cue.start * scale, "end": cue.end * scale}) for cue in cues
    ]
