"""Deliberate silences in a lesson: written as [pause Ns] markers, recorded as real silence.

A teacher pauses before revealing an answer or after a key result. Text to
speech ignores such intent, so the script marks it ("Is three a factor of
twelve? [pause 3s] Yes, because ..."). The marker is never spoken or shown:
it is taken out of the narration and kept as "after sentence k, N seconds".
Once the scene's audio is recorded and aligned, that much silence is spliced
in where sentence k ends, and every later timing moves, so the subtitle and
the picture of sentence k simply stay on screen through the pause.
"""

from __future__ import annotations

import logging
import re
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from scholarmotion.schemas import Pause, TimingCue, TTSResult

logger = logging.getLogger(__name__)

MARKER = re.compile(
    r"\[\s*pause\s*(?:for\s*)?(\d+(?:\.\d+)?)?\s*(?:s|secs?|seconds?)?\s*\]", re.IGNORECASE
)
_TOKEN = re.compile(r"\s*QZPAUSE([A-Z])QZ\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
DEFAULT_SECONDS = 2.0
MAX_SECONDS = 8.0
# The most silence one scene may add, so pauses never swamp the explanation.
MAX_SCENE_SECONDS = 20.0


def sentences(text: str) -> list[str]:
    """The narration's sentences, split the way scene cues are."""
    return [item.strip() for item in _SENTENCE_END.split(text.strip()) if item.strip()]


def speakable_with_pauses(
    text: str, speak: Callable[[str], str] = lambda value: value
) -> tuple[str, list[Pause]]:
    """``text`` without pause markers (after ``speak``), and the pauses they asked for.

    Markers become letter-only tokens before ``speak`` runs, so its rewriting
    (units, abbreviations) cannot alter them, and sentence positions are read
    from the final spoken text.
    """
    asked: list[float] = []

    def token(match: re.Match) -> str:
        if len(asked) >= 26:
            return " "
        asked.append(min(MAX_SECONDS, float(match.group(1) or DEFAULT_SECONDS)))
        return f" QZPAUSE{chr(65 + len(asked) - 1)}QZ "

    spoken = speak(MARKER.sub(token, text))
    pauses: dict[int, float] = {}
    for match in _TOKEN.finditer(spoken):
        before = _TOKEN.sub(" ", spoken[: match.start()])
        if not before.strip():
            continue  # a pause before anything is said is no pause
        index = len(sentences(before)) - 1
        seconds = asked[ord(match.group(1)) - 65]
        pauses[index] = min(MAX_SECONDS, pauses.get(index, 0.0) + seconds)
    clean = re.sub(r"\s+([.,!?;:])", r"\1", _TOKEN.sub(" ", spoken))
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, [
        Pause(after_sentence=index, seconds=round(seconds, 2))
        for index, seconds in sorted(pauses.items())
    ]


def with_markers(text: str, pauses: list[Pause]) -> str:
    """The narration with its pauses written back as markers, for a model to revise."""
    parts = sentences(text)
    for pause in pauses:
        if pause.after_sentence < len(parts):
            parts[pause.after_sentence] += f" [pause {pause.seconds:g}s]"
    return " ".join(parts)


def map_pauses(pauses: list[Pause], written: int, spoken: int) -> list[Pause]:
    """Move pauses to the spoken narration's sentences when a translation has more or fewer."""
    if written == spoken or written <= 0 or spoken <= 0:
        return [pause for pause in pauses if pause.after_sentence < max(spoken, 1)]
    mapped: dict[int, float] = {}
    for pause in pauses:
        index = min(spoken - 1, max(0, round((pause.after_sentence + 1) * spoken / written) - 1))
        mapped[index] = min(MAX_SECONDS, mapped.get(index, 0.0) + pause.seconds)
    return [
        Pause(after_sentence=index, seconds=seconds) for index, seconds in sorted(mapped.items())
    ]


def _quietest(samples: np.ndarray, rate: int, around: float, reach: float = 0.35) -> float:
    """The quietest moment within ``reach`` seconds of ``around``, so speech is never cut."""
    window = max(1, int(rate * 0.02))
    first = max(0, int((around - reach) * rate))
    last = min(len(samples), int((around + reach) * rate))
    if last - first < window * 2:
        return around
    chunk = samples[first:last]
    count = len(chunk) // window
    energy = np.sqrt(np.mean(chunk[: count * window].reshape(count, window) ** 2, axis=1))
    return (first + (int(np.argmin(energy)) + 0.5) * window) / rate


def _shift(cue: TimingCue, at: float, seconds: float) -> TimingCue:
    start = cue.start + seconds if cue.start >= at else cue.start
    end = cue.end + seconds if cue.end > at else cue.end
    return cue.model_copy(update={"start": start, "end": max(start + 0.001, end)})


def insert_pauses(audio: TTSResult, pauses: list[Pause], output: Path) -> TTSResult:
    """Splice each pause's silence into the recording where its sentence ends.

    Returns ``audio`` unchanged when there is nothing to insert or the file is
    not 16-bit PCM WAV; a lesson without its pauses is better than no lesson.
    """
    cues = audio.sentence_timings
    if not pauses or not cues:
        return audio
    try:
        with wave.open(str(audio.audio_path), "rb") as source:
            params = source.getparams()
            raw = source.readframes(params.nframes)
    except (OSError, wave.Error, EOFError) as error:
        logger.warning("Pauses not inserted into %s: %s", audio.audio_path, error)
        return audio
    if params.sampwidth != 2:
        logger.warning("Pauses not inserted: %s is not 16-bit PCM", audio.audio_path)
        return audio
    rate, frame = params.framerate, params.sampwidth * params.nchannels
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if params.nchannels > 1:
        samples = samples.reshape(-1, params.nchannels).mean(axis=1)
    budget, points = MAX_SCENE_SECONDS, []
    for pause in sorted(pauses, key=lambda item: item.after_sentence):
        index = min(pause.after_sentence, len(cues) - 1)
        seconds = min(pause.seconds, budget)
        if seconds <= 0:
            break
        budget -= seconds
        # The boundary is where the next sentence starts; the scene's end after the last.
        boundary = cues[index + 1].start if index + 1 < len(cues) else audio.duration_seconds
        # Never after the next sentence's first word starts, or the pause would
        # belong to that sentence instead.
        at = (
            boundary
            if index + 1 >= len(cues)
            else min(boundary, _quietest(samples, rate, boundary - 0.2, reach=0.2))
        )
        points.append((min(at, audio.duration_seconds), seconds))
    sentence_timings, word_timings = list(cues), list(audio.word_timings)
    # Latest first, so earlier sample positions and times stay valid.
    for at, seconds in sorted(points, reverse=True):
        cut = min(len(raw), int(at * rate) * frame)
        raw = raw[:cut] + b"\x00" * (int(seconds * rate) * frame) + raw[cut:]
        sentence_timings = [_shift(cue, at, seconds) for cue in sentence_timings]
        word_timings = [_shift(cue, at, seconds) for cue in word_timings]
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setparams(params)
        target.writeframes(raw)
    added = sum(seconds for _, seconds in points)
    # The last cue always runs to the end of the recording.
    duration = audio.duration_seconds + added
    sentence_timings[-1] = sentence_timings[-1].model_copy(update={"end": duration})
    return audio.model_copy(
        update={
            "audio_path": str(output),
            "duration_seconds": duration,
            "sentence_timings": sentence_timings,
            "word_timings": word_timings,
        }
    )
