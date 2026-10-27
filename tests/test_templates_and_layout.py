"""Template-driven code generation, cue timing and stroke-aware layout checks."""

from __future__ import annotations

import pytest

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.manim_runtime.templates import TEMPLATES, choose_template, with_cues
from scholarmotion.manim_runtime.validator import validate_generated_code
from scholarmotion.schemas import SceneSpec, VisualBeat
from scholarmotion.verification.layout import verify_layout


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Add the causal mask to the attention score matrix before softmax", "matrix_steps"),
        ("A 3x3 kernel slides over the image grid with stride one", "grid_sweep"),
        ("Derive the formula and plot the curve of the function", "equation_steps"),
        ("The force on the charge in a magnetic field", "labeled_diagram"),
        ("Something with no visual vocabulary at all", "labeled_diagram"),
    ],
)
def test_template_choice_follows_the_visual_plan(text, expected):
    assert choose_template(text).name == expected


def test_every_template_passes_the_sandbox_validator():
    for template in TEMPLATES:
        result = validate_generated_code(template.source)
        assert result.accepted, (template.name, result.errors)
        assert result.scene_classes == ["TemplateScene"]


def test_measured_cue_durations_replace_the_template_placeholders():
    code = "CUES = [\n    4.0,\n    4.0,\n]\nclass A: pass\n"
    assert with_cues(code, [1.234, 0.1]).startswith("CUES = [1.23, 0.20]\n")
    assert with_cues(code, []) == code
    numpy_form = "    CUES = np.array([8.0, 4.0])\nx = 1\n"
    assert with_cues(numpy_form, [1.0, 2.0]) == "    CUES = [1.00, 2.00]\nx = 1\n"
    assert with_cues("CUES = (3, 4)\n", [5.0]) == "CUES = [5.00]\n"


@pytest.mark.asyncio
async def test_prompt_carries_template_and_cue_plan_and_code_gets_exact_cues():
    class Provider:
        name = "openai_compatible"
        prompt = ""

        async def generate_code(self, prompt):
            self.prompt = prompt
            return (
                "from manim import *\nCUES = [9.0]\nclass TemplateScene(Scene):\n"
                "    def construct(self):\n        pass\n"
            )

    spec = SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Masking the scores",
        learning_objective="Show how the mask removes future positions.",
        narration="",
        duration_target_seconds=8,
        visual_beats=[VisualBeat(beat_id="b1", narration_segment="x", visual="score matrix")],
    )
    provider = Provider()
    generated = await generate_scene_code(
        provider,
        spec,
        narration="We start with the scores. Then we add the mask.",
        sentence_durations=[3.5, 4.25],
    )
    assert "WORKING TEMPLATE (matrix_steps" in provider.prompt
    assert 'cue 2: 4.25s  "Then we add the mask."' in provider.prompt
    assert "CUES = [3.50, 4.25]" in generated.python_code
    assert "class SceneS01(Scene)" in generated.python_code


def _label(name="MathTex_1", left=0.0, right=1.0, bottom=0.0, top=0.4, font_size=40.0):
    return ObjectBounds(
        name, left, right, bottom, top, text_height=top - bottom, font_size=font_size
    )


def test_label_beside_a_slanted_line_is_not_an_overlap():
    # The label sits inside the line's bounding box but away from its path.
    line = ObjectBounds("Line_2", -2, 2, -2, 2, points=[[x / 10, x / 10] for x in range(-20, 21)])
    label = _label(left=0.6, right=1.6, bottom=-0.9, top=-0.4)
    assert not [i for i in verify_layout("S", [line, label]) if i.category == "layout.overlap"]


def test_label_on_top_of_a_line_is_still_an_overlap():
    line = ObjectBounds("Line_2", -2, 2, -2, 2, points=[[x / 10, x / 10] for x in range(-20, 21)])
    label = _label(left=-0.5, right=0.5, bottom=-0.2, top=0.2)
    assert [i for i in verify_layout("S", [line, label]) if i.category == "layout.overlap"]


def test_strike_out_cross_over_an_equation_is_intentional():
    equation = _label(name="STEMEquation_6", left=-1, right=1, bottom=-0.3, top=0.3)
    cross = ObjectBounds("Cross_10", -1, 1, -0.3, 0.3)
    assert not [i for i in verify_layout("S", [equation, cross]) if i.category == "layout.overlap"]


def test_text_size_uses_effective_font_size_when_known():
    short_letter = _label(bottom=0.0, top=0.12, font_size=40.0)  # lowercase "v"
    shrunk = _label(name="MathTex_3", left=3, right=4, bottom=0.0, top=0.3, font_size=20.0)
    issues = verify_layout("S", [short_letter, shrunk])
    small = [i.objects[0] for i in issues if i.category == "layout.text_too_small"]
    assert small == ["MathTex_3"]


MASK = r"M = \begin{pmatrix} 0 & -\infty \\ 0 & 0 \end{pmatrix}"


def test_required_matrix_drawn_with_manim_matrix_counts_as_present():
    from scholarmotion.verification.math import verify_required_equations

    code = 'm = Matrix([["0", r"-\\infty"], ["0", "0"]])\nlabel = MathTex("M")\n'
    assert not verify_required_equations("S01", [MASK], code)


def test_required_matrix_with_a_wrong_entry_is_still_missing():
    from scholarmotion.verification.math import verify_required_equations

    code = 'm = Matrix([["0", "0"], ["0", "0"]])\n'
    assert verify_required_equations("S01", [MASK], code)


def test_cue_clock_ends_exactly_with_the_narration():
    from scholarmotion.manim_runtime.components import CueClock

    class FakeScene:
        def __init__(self):
            self.elapsed = 0.0

        def play(self, *_, run_time, **__):
            self.elapsed += run_time

        def wait(self, seconds):
            self.elapsed += seconds

    scene = FakeScene()
    clock = CueClock(scene, [2.0, 3.0, 1.5])
    clock.play("a", run_time=1.0)
    clock.next_cue()
    clock.play("b", run_time=10.0)  # too long: shortened to fit its cue
    clock.next_cue()
    clock.play("c", run_time=0.5)
    clock.finish()
    assert scene.elapsed == pytest.approx(6.5)
