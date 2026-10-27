from pathlib import Path

from scholarmotion.agents.scene_compiler import compile_scene_specs
from scholarmotion.agents.script_writer import _constrain_spoken_word_budget
from scholarmotion.schemas import NarrationBlock, TimingCue, TTSResult, VisualBeat
from scholarmotion.services.orchestration import ScholarMotionPipeline


def test_a_requested_length_overrides_the_saved_setting():
    from scholarmotion.agents.learner_profiler import profile_request
    from scholarmotion.agents.lesson_preparation import LessonPreparation

    profile = profile_request(
        "Newton's second law for beginners.",
        target_duration_minutes=3,
        preparation=LessonPreparation(duration_minutes=1),
    )
    assert profile.desired_duration_minutes == 1


def test_scene_audio_is_normalized_to_the_planned_duration_and_rescales_cues(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"original")

    class Assembler:
        def retime_wav(self, _source, _target, output):
            Path(output).write_bytes(b"retimed")
            return True

        def probe_duration(self, path):
            return 32.0 if ".timed-" in path else 30.0

    pipeline = object.__new__(ScholarMotionPipeline)
    pipeline.assembler = Assembler()
    audio = TTSResult(
        audio_path=str(source),
        duration_seconds=30,
        sentence_timings=[TimingCue(text="Sentence.", start=3, end=15)],
        word_timings=[TimingCue(text="Sentence", start=3, end=15)],
    )

    fitted = pipeline._fit_narration_to_scene_budget(audio, target_seconds=32, artifact_version=2)

    assert fitted.duration_seconds == 32
    assert Path(fitted.audio_path).read_bytes() == b"retimed"
    assert fitted.sentence_timings[0].start == 3.2
    assert fitted.sentence_timings[0].end == 16


def test_scene_compiler_does_not_expand_a_short_planned_scene():
    block = NarrationBlock(
        block_id="N01",
        chapter_id="C01",
        text="A concise explanation.",
        learning_objective="Explain the idea.",
        estimated_duration_seconds=12,
    )
    specs = compile_scene_specs(
        [block],
        {
            "N01": [
                VisualBeat(
                    beat_id="B01",
                    narration_segment="A concise explanation.",
                    visual="A clear diagram.",
                    primitive="diagram",
                    start_seconds=0,
                    end_seconds=12,
                )
            ]
        },
    )

    assert specs[0].duration_target_seconds == 12


def test_provider_narration_is_limited_to_its_scene_word_budget():
    text = (
        "First complete sentence. "
        "Second complete sentence with useful detail. "
        "Third complete sentence that should not fit in the short slot."
    )

    constrained = _constrain_spoken_word_budget(text, 5)

    assert constrained.endswith(".")
    assert len(constrained.split()) <= 18
