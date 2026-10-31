"""[pause Ns] markers become real silence in the narration, never spoken words."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from scholarmotion.agents.script_writer import (
    SCRIPT_SHAPE_ROUNDS,
    LessonScript,
    ScriptReview,
    ScriptSegment,
    revise_narration,
    write_script,
)
from scholarmotion.media.pauses import (
    insert_pauses,
    map_pauses,
    speakable_with_pauses,
    with_markers,
)
from scholarmotion.media.speech import to_speakable
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, Pause, TimingCue, TTSResult


def test_markers_leave_the_text_and_attach_to_the_sentence_they_follow():
    text, pauses = speakable_with_pauses(
        "Is 3 a factor of 12? [pause 3s] Yes, because 12 is 3 times 4. "
        "Now try 5. [Pause] It is not. [pause 20 seconds]",
        to_speakable,
    )
    assert "pause" not in text.lower() and "[" not in text
    assert text.startswith("Is 3 a factor of 12? Yes")
    # Default 2 s, and no pause is longer than 8 s.
    assert pauses == [
        Pause(after_sentence=0, seconds=3),
        Pause(after_sentence=2, seconds=2),
        Pause(after_sentence=3, seconds=8),
    ]


def test_a_marker_survives_speech_clean_up_that_rewrites_units():
    # to_speakable spells out units after numbers; the marker must not be touched.
    text, pauses = speakable_with_pauses(
        "It moves at 3 m/s. [pause 2s] Then it stops.", to_speakable
    )
    assert text == "It moves at 3 meters per second. Then it stops."
    assert pauses == [Pause(after_sentence=0, seconds=2)]


def test_markers_round_trip_for_a_reviser_and_follow_a_translation():
    pauses = [Pause(after_sentence=0, seconds=3)]
    marked = with_markers("Question? Answer.", pauses)
    assert marked == "Question? [pause 3s] Answer."
    assert speakable_with_pauses(marked) == ("Question? Answer.", pauses)
    # A translation that splits each sentence in two keeps the pause after the question.
    assert map_pauses(pauses, 2, 4) == [Pause(after_sentence=1, seconds=3)]


def _wav(path, seconds: float, rate: int = 8000) -> None:
    tone = (np.sin(np.arange(int(seconds * rate)) * 0.3) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(tone.tobytes())


def test_silence_is_spliced_in_and_later_timings_move(tmp_path):
    source = tmp_path / "scene.wav"
    _wav(source, 4.0)
    audio = TTSResult(
        audio_path=str(source),
        duration_seconds=4.0,
        sentence_timings=[
            TimingCue(text="Question?", start=0, end=2),
            TimingCue(text="Answer.", start=2, end=4),
        ],
        word_timings=[
            TimingCue(text="Question?", start=0, end=1.8),
            TimingCue(text="Answer.", start=2, end=3.5),
        ],
    )
    paused = insert_pauses(audio, [Pause(after_sentence=0, seconds=3)], tmp_path / "paused.wav")

    assert paused.duration_seconds == pytest.approx(7.0)
    with wave.open(paused.audio_path, "rb") as result:
        assert result.getnframes() == 7 * 8000
        frames = np.frombuffer(result.readframes(result.getnframes()), dtype="<i2")
    # The question's cue holds through the silence; the answer starts after it.
    question, answer = paused.sentence_timings
    assert question.start == 0 and answer.start == pytest.approx(question.end)
    assert 4.7 <= answer.start <= 5.0
    assert paused.word_timings[1].start == pytest.approx(answer.start)
    assert not frames[int(2.5 * 8000) : int(4.5 * 8000)].any()


def test_unreadable_audio_keeps_the_narration_without_pauses(tmp_path):
    audio = TTSResult(
        audio_path=str(tmp_path / "missing.wav"),
        duration_seconds=2,
        sentence_timings=[TimingCue(text="A.", start=0, end=2)],
    )
    assert insert_pauses(audio, [Pause(after_sentence=0, seconds=2)], tmp_path / "x.wav") is audio


@pytest.mark.asyncio
async def test_an_edit_can_add_pauses_through_the_reviser():
    class Reviser:
        name = "openai_compatible"

        async def generate_text(self, prompt):
            assert "[pause" in prompt  # the reviser is told what the markers mean
            return "Q1: is 4 even? [pause 3s] Yes. Q2: is 9 even? [pause 3s] No."

    revised = await revise_narration(Reviser(), "Q1: is 4 even? Yes.", "pause before answers")
    text, pauses = speakable_with_pauses(revised)
    assert "[pause" not in text
    assert [pause.after_sentence for pause in pauses] == [0, 2]


@pytest.mark.asyncio
async def test_rewriting_the_last_scene_keeps_the_lesson_ending():
    prompts = []

    class Reviser:
        name = "openai_compatible"

        async def generate_text(self, prompt):
            prompts.append(prompt)
            return "A recap. Thanks for learning with me."

    await revise_narration(Reviser(), "Practice.", "more depth", is_closing=True)
    await revise_narration(Reviser(), "Practice.", "more depth")
    assert "ENDING:" in prompts[0] and "last scene" in prompts[0]
    assert "ENDING:" not in prompts[1]


@pytest.mark.asyncio
async def test_the_script_review_gets_one_polish_pass_for_minor_notes():
    calls = []

    class Writer:
        name = "openai_compatible"

        async def generate_structured(self, prompt, schema):
            calls.append(schema.__name__)
            if schema is LessonScript:
                narration = "Why does ice float? [pause 2s] Because it is less dense."
                return LessonScript(segments=[ScriptSegment(narration=narration, title="Ice")])
            # Minor notes every time: one polishing revision, then accepted.
            return ScriptReview(approved=True, problems=["the bridge could be smoother"])

    plan = CurriculumPlan(
        topic="ice",
        items=[CurriculumItem(title="Ice", objective="Explain floating", duration_seconds=30)],
    )
    blocks = await write_script(Writer(), plan, {"evidence": []})

    assert calls == ["LessonScript", "ScriptReview", "LessonScript", "ScriptReview"]
    assert blocks[0].text == "Why does ice float? Because it is less dense."
    assert blocks[0].pauses == [Pause(after_sentence=0, seconds=2)]


def _two_chapter_plan() -> CurriculumPlan:
    return CurriculumPlan(
        topic="ice",
        items=[
            CurriculumItem(title="Ice", objective="Explain floating", duration_seconds=30),
            CurriculumItem(title="Density", objective="Compare densities", duration_seconds=30),
        ],
    )


@pytest.mark.asyncio
async def test_a_draft_that_misses_segments_is_sent_back_with_feedback_until_it_fits():
    prompts = []

    class Writer:
        name = "openai_compatible"

        async def generate_structured(self, prompt, schema):
            prompts.append(prompt)
            if schema is ScriptReview:
                return ScriptReview(approved=True)
            drafts = sum(1 for p in prompts if "Return exactly" in p)
            # Merged into one segment, then an empty one, then right.
            if drafts == 1:
                return LessonScript(segments=[ScriptSegment(narration="Ice floats.")])
            second = "" if drafts == 2 else "Ice is less dense than water."
            return LessonScript(
                segments=[ScriptSegment(narration="Ice floats."), ScriptSegment(narration=second)]
            )

    blocks = await write_script(Writer(), _two_chapter_plan(), {"evidence": []})

    assert [block.text for block in blocks] == ["Ice floats.", "Ice is less dense than water."]
    assert "has 1 segments but the plan needs exactly 2" in prompts[1]
    assert "Segments 2 have no narration" in prompts[2]


@pytest.mark.asyncio
async def test_a_draft_that_never_fits_the_plan_stops_after_its_revisions():
    class Writer:
        name = "openai_compatible"
        drafts = 0

        async def generate_structured(self, prompt, schema):
            self.drafts += 1
            return LessonScript(segments=[ScriptSegment(narration="Ice floats.")])

    writer = Writer()
    with pytest.raises(ValueError, match="does not cover all planned segments"):
        await write_script(writer, _two_chapter_plan(), {"evidence": []})
    assert writer.drafts == 1 + SCRIPT_SHAPE_ROUNDS


@pytest.mark.asyncio
async def test_the_code_model_is_told_to_hold_the_picture_through_a_pause():
    from scholarmotion.agents.code_generator import generate_scene_code
    from scholarmotion.schemas import SceneSpec, VisualBeat

    spec = SceneSpec(
        scene_id="S05",
        chapter_id="C1",
        title="Practice",
        learning_objective="Check factors",
        narration="Is 3 a factor of 12? Yes, because 12 is 3 times 4.",
        duration_target_seconds=8,
        visual_beats=[VisualBeat(beat_id="b", narration_segment="Is 3", visual="Question card")],
        pauses=[Pause(after_sentence=0, seconds=3)],
    )
    seen = {}

    class Code:
        name = "openai_compatible"

        async def generate_code(self, prompt, *, cached_prefix=None, purpose="code", images=None):
            seen["prompt"] = prompt
            return "from manim import *\nclass SceneS05(Scene):\n    def construct(self):\n        pass\n"

    await generate_scene_code(Code(), spec, narration=spec.narration, sentence_durations=[5.0, 3.0])
    cue_one = next(line for line in seen["prompt"].splitlines() if "cue 1:" in line)
    assert "silent for 3s" in cue_one and "answer" in cue_one
    assert "silent" not in next(line for line in seen["prompt"].splitlines() if "cue 2:" in line)
