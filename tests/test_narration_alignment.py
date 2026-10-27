"""Measured narration timings drive scene cuts, subtitles and animation cues."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from scholarmotion.media.alignment import (
    _fill_gaps,
    _viterbi,
    scene_cuts,
    sentence_timings_from_words,
    words_between,
)
from scholarmotion.media.audio import align_visual_beats
from scholarmotion.media.narration import split_lesson_wav
from scholarmotion.schemas import TimingCue, VisualBeat


def _cue(text: str, start: float, end: float) -> TimingCue:
    return TimingCue(text=text, start=start, end=end)


def test_viterbi_follows_the_emitted_characters():
    blank, a, b = 0, 1, 2
    frames = [blank, a, a, blank, blank, b, blank]
    emissions = np.full((len(frames), 3), -10.0)
    for index, token in enumerate(frames):
        emissions[index, token] = 0.0
    path = _viterbi(emissions, [a, b], blank)
    assert path.tolist() == [-1, 0, 0, -1, -1, 1, -1]


def test_viterbi_refuses_text_longer_than_the_audio():
    assert _viterbi(np.zeros((2, 3)), [1, 2, 1], 0) is None


def test_unspeakable_words_take_the_gap_between_neighbours():
    cues = _fill_gaps(["x", "—", "y"], [(0.0, 1.0), None, (2.0, 3.0)], 3.0)
    assert (cues[1].start, cues[1].end) == (1.0, 2.0)


def test_scene_cut_lands_in_the_pause_between_scenes():
    words = [_cue("a", 0, 1), _cue("b", 1, 2), _cue("c", 3, 4)]
    assert scene_cuts(words, [2, 1]) == [2.5]
    assert scene_cuts(words, [1, 1]) is None  # counts must cover every word


def test_scene_words_are_rebased_on_their_own_clip():
    words = words_between([_cue("c", 3, 4)], 2.5, 1.5)
    assert (words[0].start, words[0].end) == (0.5, 1.5)


def test_sentences_start_with_their_first_spoken_word():
    text = "One two. Three four."
    words = [
        _cue("One", 0.2, 0.5),
        _cue("two.", 0.5, 1),
        _cue("Three", 2.4, 2.8),
        _cue("four.", 2.8, 3),
    ]
    cues = sentence_timings_from_words(text, words, 3.5)
    assert [(c.start, c.end) for c in cues] == [(0.0, 2.4), (2.4, 3.5)]


def test_visual_beats_use_measured_word_starts():
    narration = "First idea here. Second idea now."
    beats = [
        VisualBeat(beat_id="a", narration_segment="First idea here.", visual="x"),
        VisualBeat(beat_id="b", narration_segment="Second idea now.", visual="y"),
    ]
    words = [_cue(w, i, i + 0.5) for i, w in enumerate(narration.split())]
    words[3] = _cue("Second", 4.2, 4.6)
    timed = align_visual_beats(narration, beats, 8.0, word_timings=words)
    assert timed[1].start_seconds == pytest.approx(4.2)


def test_split_lesson_wav_honours_measured_cuts(tmp_path: Path):
    rate = 8000
    lesson = tmp_path / "lesson.wav"
    with wave.open(str(lesson), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(np.zeros(rate * 4, dtype="<i2").tobytes())
    outputs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    durations = split_lesson_wav(lesson, [3, 3], outputs, cuts=[1.5])
    assert durations == [1.5, 2.5]
