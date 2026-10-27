from scholarmotion.schemas import SceneSpec, VerificationIssue, VisualBeat
from scholarmotion.verification.aggregator import aggregate_reports
from scholarmotion.verification.pedagogy import verify_rendered_storyboard


def _scene() -> SceneSpec:
    return SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Electric flux",
        learning_objective="Explain electric flux.",
        duration_target_seconds=30,
        narration="Flux measures the field through a surface.",
        visual_beats=[
            VisualBeat(
                beat_id="b1",
                narration_segment="N01",
                visual="Introduce one labeled visual model.",
            )
        ],
    )


def test_storyboard_directions_must_not_be_rendered_as_lesson_text():
    code = 'heading = Text("Introduce one labeled visual model.")'

    issues = verify_rendered_storyboard(_scene(), code)

    assert len(issues) == 1
    assert issues[0].category == "pedagogy.stage_direction_rendered"
    assert issues[0].severity == "high"
    assert not verify_rendered_storyboard(_scene(), 'heading = Text("Electric flux")')
    assert not verify_rendered_storyboard(
        _scene(), '# Introduce one labeled visual model.\nheading = Text("Electric flux")'
    )


def test_multiple_readability_warnings_do_not_pass_verification():
    issues = [
        VerificationIssue(
            scene_id="S01",
            category="layout.text_too_small",
            severity="medium",
            confidence=1,
            description="Text is too small.",
            suggested_repair="Increase the font size.",
        )
        for _ in range(2)
    ]

    report = aggregate_reports("S01", {"layout": issues})

    assert report.score == 0.7
    assert report.passed is False


def test_spoken_sentence_cannot_be_used_as_a_manim_caption():
    scene = _scene().model_copy(
        update={
            "narration": "Imagine a charge inside a sphere. The field lines spread out as the sphere grows.",
        }
    )

    issues = verify_rendered_storyboard(
        scene, 'caption = Text("The field lines spread out as the sphere grows.")'
    )

    assert any(issue.category == "pedagogy.spoken_text_rendered" for issue in issues)
