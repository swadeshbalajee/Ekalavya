"""Respell words for the speech engine only; subtitles keep how they are written.

A learner says the narrator mispronounces "Euler". The narration (and so the
subtitles) must still read "Euler", but the speech engine is given "Oiler".
After the recording is aligned, each cue's text is mapped back to the written
spelling, so nothing on screen changes.
"""

from __future__ import annotations

import re

from scholarmotion.schemas import TTSResult


def _pattern(word: str) -> re.Pattern:
    # A plural is the same word said the same way: "wavelengths" respells as
    # "wave-lengths", the ending kept as written.
    return re.compile(rf"(?<!\w){re.escape(word)}(?=(?:e?s)?(?!\w))", re.IGNORECASE)


def respell(text: str, pronunciations: dict[str, str]) -> str:
    """``text`` with every listed word replaced by its spoken spelling."""
    for word, spoken in sorted(pronunciations.items(), key=lambda item: -len(item[0])):
        text = _pattern(word).sub(spoken, text)
    return text


def restore_spelling(audio: TTSResult, pronunciations: dict[str, str]) -> TTSResult:
    """The recording's cues with the written spellings back in their text."""
    if not pronunciations:
        return audio
    back = {spoken: word for word, spoken in pronunciations.items()}
    return audio.model_copy(
        update={
            "sentence_timings": [
                cue.model_copy(update={"text": respell(cue.text, back)})
                for cue in audio.sentence_timings
            ],
            "word_timings": [
                cue.model_copy(update={"text": respell(cue.text, back)})
                for cue in audio.word_timings
            ],
        }
    )


def uses_any(text: str, words) -> bool:
    return any(_pattern(word).search(text) for word in words)
