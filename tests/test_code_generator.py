import ast

import pytest

from scholarmotion.agents.code_generator import (
    _fallback_scene_code,
    _latex_to_unicode,
    generate_scene_code,
    normalize_manim_direction_constants,
    scene_class_name,
)
from scholarmotion.manim_runtime.validator import validate_generated_code
from scholarmotion.providers.llm.mock import MockLLMProvider
from scholarmotion.schemas import SceneSpec, VisualBeat


def _scene(scene_id: str) -> SceneSpec:
    return SceneSpec(
        scene_id=scene_id,
        chapter_id="C01",
        title="A startup scene",
        learning_objective="Demonstrate a valid generated class name.",
        duration_target_seconds=5,
        narration="A short scene.",
        visual_beats=[
            VisualBeat(
                beat_id="B01",
                narration_segment="A short scene.",
                visual="Title",
                start_seconds=0,
                end_seconds=5,
            )
        ],
    )


def test_scene_class_name_normalizes_external_identifier() -> None:
    assert scene_class_name("startup-smoke") == "Scenestartup_smoke"


def test_unsupported_manim_direction_names_are_normalized():
    code = "point = UP_LEFT + DOWN_RIGHT"

    assert "point = UL + DR" in normalize_manim_direction_constants(code)


def test_common_model_color_names_are_normalized():
    result = normalize_manim_direction_constants(
        "fill_color = GRAY_DARKER\npipe_color = BLUE_GRAY\nfield_color = CYAN"
    )

    assert "fill_color = GRAY_D" in result
    assert "pipe_color = BLUE_E" in result
    assert "field_color = TEAL_A" in result


def test_tex_based_title_constructor_uses_unicode_safe_title():
    result = normalize_manim_direction_constants(
        "from manim import *\ntitle = Title('Gauss law')", latex_enabled=False
    )

    assert "title = SafeTitle('Gauss law')" in result


def test_prompt_framing_never_leaks_into_learner_facing_title():
    result = normalize_manim_direction_constants(
        "title = SafeTitle('Create a two-minute beginner visual lesson')",
        display_title="Bicycle balance",
    )

    assert "SafeTitle('Bicycle balance')" in result
    assert "Create a two-minute" not in result


def test_model_hallucinated_boundary_and_dashed_style_are_normalized():
    result = normalize_manim_direction_constants(
        "point = circle.get_boundary()\nsurface = Circle(radius=1).set_stroke(style=DASHED)"
    )

    assert "circle.get_center()" in result
    assert "DashedVMobject(Circle(radius=1))" in result
    assert "style=DASHED" not in result


def test_curved_arrow_short_keyword_names_are_normalized_for_manim_ce():
    result = normalize_manim_direction_constants("turn = CurvedArrow(start=LEFT, end=UP)")

    assert "CurvedArrow(start_point=LEFT, end_point=UP)" in result


def test_known_manim_constructor_mistakes_are_normalized():
    result = normalize_manim_direction_constants(
        "highlight = SurroundRectangle(triangle)\n"
        "shape = Triangle(sides=[3, 5, 4])\n"
        "point = [0, 0, 0] + UP\n"
        "shape = Polygon(Point(0, 0), Point(4, 0), Point(0, 3))\n"
    )

    assert "SurroundingRectangle(triangle)" in result
    assert "Polygon(" in result
    assert "sides=" not in result
    assert "import numpy as np" in result
    assert "np.array([0, 0, 0]) + UP" in result
    assert "Polygon(np.array([0, 0, 0]), np.array([4, 0, 0]), np.array([0, 3, 0]))" in result


def test_animation_lag_is_normalized_to_supported_manim_arguments():
    result = normalize_manim_direction_constants("self.play(FadeIn(group, lag=0.3), lag=0.5)")

    assert "FadeIn(group)" in result
    assert "self.play(FadeIn(group), lag_ratio=0.5)" in result


def test_cleanup_groups_use_group_for_heterogeneous_scene_mobjects():
    result = normalize_manim_direction_constants("self.play(FadeOut(VGroup(*self.mobjects)))")

    assert "FadeOut(Group(*self.mobjects))" in result


def test_set_x_and_y_normalize_vector_coordinates_to_scalars():
    result = normalize_manim_direction_constants(
        "arrow.set_x(circuit.get_right() + 0.5).set_y(circuit.get_center())"
    )

    assert "set_x((circuit.get_right() + 0.5)[0])" in result
    assert "set_y(circuit.get_center()[1])" in result


def test_common_manim_api_and_helper_mistakes_are_normalized():
    result = normalize_manim_direction_constants(
        "from manim import RightTriangle\n"
        "from manim.mobject.geometry.shape_creation import Polygon\n"
        "from manim.mobject.svg.text_utils import Text\n"
        "from typing import Callable\n"
        "from scholarmotion.manim_runtime.primitives import *\n"
        "class SceneS01(Scene):\n"
        "    def construct(self):\n"
        "        shape = RightTriangle()\n"
        "        shape.align_to(LEFT, aligned_edge=LEFT)\n"
        "        shape.to_center()\n"
        "        line = always_redirected(lambda: shape)\n"
        "        title = SafeTitle(text='Ohm law', title='Voltage')\n"
    )

    assert "from __future__ import annotations" in result
    assert "from scholarmotion.manim_runtime.components import" in result
    assert "from typing import" not in result
    assert "from manim import Triangle" in result
    assert "from manim.mobject" not in result
    assert "primitives" not in result
    assert "Triangle()" in result
    assert "aligned_edge" not in result
    assert "move_to(ORIGIN)" in result
    assert "always_redraw" in result
    assert "SafeTitle" in result
    assert "subtitle='Voltage'" in result


def test_no_latex_mode_rewrites_equations_assets_and_missing_manimer_names():
    result = normalize_manim_direction_constants(
        "from scholarmotion.manim_runtime.components import Scene, SceneSpec, SafeTitle\n"
        "equation = MathTex(r'\\phi = E A \\cos \\theta')\n"
        "panel = EquationPanel(equation=r'V = I \\times R')\n"
        "image = SVGMobject('voltage_source.svg')\n"
        "point = current.get_center() + R * 0.5\n",
        latex_enabled=False,
    )

    assert "from scholarmotion.manim_runtime.components import Scene" not in result
    assert "SceneSpec" not in result
    assert "from manim import *" in result
    assert "MathTex" not in result and "EquationPanel" not in result
    assert "φ = E A cos θ" in result
    assert "V = I × R" in result
    assert "SVGMobject" not in result
    assert "Text('Voltage Source')" in result or 'Text("Voltage Source")' in result
    assert "RIGHT * 0.5" in result


def test_group_objects_use_fadein_instead_of_invalid_create_animation():
    result = normalize_manim_direction_constants(
        "from manim import *\n"
        "class SceneS01(Scene):\n"
        "    def construct(self):\n"
        "        group = Group(Circle(), Text('label'))\n"
        "        self.play(Create(group))\n"
        "        self.play(Create(Group(Circle(), Text('other'))))\n"
    )

    assert result.count("FadeIn(") == 2
    assert "Create(group)" not in result


def test_simplified_scene_fallback_displays_locked_stem_equation():
    scene = _scene("S03").model_copy(
        update={
            "narration": "Gauss's law says charge creates electric flux. Divergence of E equals charge density over epsilon naught.",
            "equations": ["∇ · E = ρ / ε₀"],
        }
    )

    code = _fallback_scene_code(scene, "SceneS03", sentence_durations=[2.0, 3.0])

    assert 'Text("∇ · E = ρ / ε₀"' in code
    assert "self.wait(" in code
    assert "Gauss's law says charge" not in code


def test_fallback_displays_latex_formula_as_unicode_without_losing_canonical_source():
    scene = _scene("S03").model_copy(
        update={
            "narration": "The formula is used in scaled dot-product attention.",
            "equations": ["Attention(Q, K, V) = softmax(QK^T / \\sqrt{d_k}) V"],
        }
    )

    code = _fallback_scene_code(scene, "SceneS02", sentence_durations=[4.0])

    assert "√dₖ" in code
    assert "Attention(Q, K, V) = softmax(QKᵀ / √dₖ) V" in code
    assert "The formula is used" not in code
    assert 'Text("Attention(Q, K, V) = softmax(QK^T / \\\\sqrt' not in code
    ast.parse(code)


def test_latex_text_fraction_and_delimiters_normalize_to_valid_stem_text():
    source = r"\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V"
    assert _latex_to_unicode(source) == "Attention(Q, K, V) = softmax(QKᵀ / √dₖ)V"


@pytest.mark.asyncio
async def test_maxwell_scene_uses_model_generation_with_repairable_layout():
    class Provider:
        called = False

        async def generate_code(self, _prompt):
            self.called = True
            return "class SceneS02(Scene):\n    pass"

    provider = Provider()
    scene = _scene("S02").model_copy(
        update={
            "title": "மேக்ஸ்வெல் சமன்பாடுகள்",
            "learning_objective": "மின்சார புலத்தை காஸ் விதி விளக்குகிறது.",
            "narration": "மின்னூட்டம் மின்புலத்தை உருவாக்குகிறது.",
            "equations": ["∇ · E = ρ / ε₀"],
        }
    )

    generated = await generate_scene_code(provider, scene, sentence_durations=[4.0])

    assert provider.called
    assert "class SceneS02(Scene):" in generated.python_code


@pytest.mark.asyncio
async def test_canonical_maxwell_equation_uses_model_generation_without_topic_keyword():
    class Provider:
        called = False

        async def generate_code(self, _prompt):
            self.called = True
            return "class SceneS04(Scene):\n    pass"

    provider = Provider()
    scene = _scene("S04").model_copy(
        update={
            "title": "Faraday's law",
            "learning_objective": "Explain how induction works.",
            "narration": "A changing magnetic field induces an electric field.",
            "equations": ["∇ × E = −∂B / ∂t"],
        }
    )

    generated = await generate_scene_code(provider, scene, sentence_durations=[4.0])

    assert provider.called
    assert "class SceneS04(Scene):" in generated.python_code


@pytest.mark.asyncio
async def test_maxwell_scene_tag_does_not_bypass_model_generation():
    class Provider:
        called = False

        async def generate_code(self, _prompt):
            self.called = True
            return "class SceneS01(Scene):\n    pass"

    provider = Provider()
    scene = _scene("S01").model_copy(update={"tags": ["diagram", "maxwell"]})
    generated = await generate_scene_code(provider, scene, sentence_durations=[4.0])

    assert provider.called
    assert "class SceneS01(Scene):" in generated.python_code


def test_renderer_rejects_animating_python_lists_directly():
    invalid = """from manim import *
class SceneS01(Scene):
    def construct(self):
        arrows = []
        self.play(FadeIn(arrows))
"""
    valid = invalid.replace("FadeIn(arrows)", "FadeIn(VGroup(*arrows))")

    result = validate_generated_code(invalid)
    assert not result.accepted
    assert "wrap its Mobjects in VGroup" in result.errors[0]
    assert validate_generated_code(valid).accepted


@pytest.mark.asyncio
async def test_mock_generator_uses_normalized_scene_class() -> None:
    generated = await generate_scene_code(MockLLMProvider(), _scene("startup-smoke"))
    assert generated.scene_class == "Scenestartup_smoke"
    assert "class Scenestartup_smoke(Scene):" in generated.python_code


@pytest.mark.asyncio
async def test_scene_code_prompt_includes_spoken_narration_for_alignment_and_forbids_overlays() -> (
    None
):
    class CapturingProvider:
        prompt = ""

        async def generate_code(self, prompt: str) -> str:
            self.prompt = prompt
            return "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"

    provider = CapturingProvider()
    await generate_scene_code(
        provider, _scene("S01"), narration="Show the equation after saying it."
    )

    assert "Show the equation after saying it." in provider.prompt
    assert '"narration"' not in provider.prompt
    assert "EXACT SPOKEN NARRATION" in provider.prompt
    assert "Do not create captions or spoken-text overlays" in provider.prompt


@pytest.mark.asyncio
async def test_scene_code_prompt_marks_supplied_stem_equations_as_immutable() -> None:
    class CapturingProvider:
        prompt = ""

        async def generate_code(self, prompt: str) -> str:
            self.prompt = prompt
            return "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"

    provider = CapturingProvider()
    scene = _scene("S01").model_copy(update={"equations": ["∇ · E = ρ / ε₀"]})
    await generate_scene_code(provider, scene, narration="Explain Gauss's law.")

    assert "Treat every equation in SCENE SPEC `equations` as immutable" in provider.prompt
    assert "∇ · E = ρ / ε₀" in provider.prompt


@pytest.mark.asyncio
async def test_real_provider_failure_never_substitutes_an_unrelated_template(monkeypatch) -> None:
    """A canned topic template contradicts the narration; fail the attempt instead."""
    from scholarmotion.agents import code_generator

    monkeypatch.setattr(code_generator, "CODE_RETRY_DELAY_SECONDS", 0)
    calls = []

    class UnavailableProvider:
        name = "openai_compatible"

        async def generate_code(self, _prompt: str) -> str:
            calls.append(1)
            raise TimeoutError("code endpoint timed out")

    scene = _scene("S01").model_copy(update={"title": "Scaled dot-product attention river bank"})
    with pytest.raises(code_generator.CodeGenerationError, match="after 3 attempts"):
        await generate_scene_code(UnavailableProvider(), scene)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_real_provider_recovers_on_retry(monkeypatch) -> None:
    from scholarmotion.agents import code_generator

    monkeypatch.setattr(code_generator, "CODE_RETRY_DELAY_SECONDS", 0)
    outcomes = [
        TimeoutError("busy"),
        "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n",
    ]

    class FlakyProvider:
        name = "openai_compatible"

        async def generate_code(self, _prompt: str) -> str:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    generated = await generate_scene_code(FlakyProvider(), _scene("S01"))
    assert "SCHOLARMOTION_DETERMINISTIC_FALLBACK" not in generated.python_code
    assert "class SceneS01(Scene)" in generated.python_code


@pytest.mark.asyncio
async def test_simplification_fallback_uses_visuals_without_transcribing_audio():
    class UnexpectedProviderCall:
        async def generate_code(self, _prompt: str) -> str:
            raise AssertionError("simplified final repair should use the local card template")

    generated = await generate_scene_code(
        UnexpectedProviderCall(), _scene("S01"), narration="A short voiceover.", fallback_only=True
    )

    assert 'Text("A short voiceover."' not in generated.python_code
    assert "class SceneS01(Scene):" in generated.python_code


@pytest.mark.asyncio
async def test_simplified_scene_keeps_voiceover_out_of_manim():
    class UnexpectedProviderCall:
        async def generate_code(self, _prompt: str) -> str:
            raise AssertionError("simplified final repair should use the local card template")

    narration = "First point. Second point explains why it matters. Final point."
    generated = await generate_scene_code(
        UnexpectedProviderCall(), _scene("S01"), narration=narration, fallback_only=True
    )

    code = generated.python_code
    assert 'Text("First point."' not in code
    assert 'Text("Second point explains why it matters."' not in code
    assert 'Text("Final point."' not in code


def test_lesson_topic_comes_from_the_planner_not_the_raw_request() -> None:
    from scholarmotion.agents.learner_profiler import profile_request
    from scholarmotion.agents.lesson_preparation import LessonPreparation

    planned = LessonPreparation(topic="electric flux")
    assert profile_request("Explain me what electric flux is.", preparation=planned).topic == (
        "electric flux"
    )


def test_offline_fallback_shows_only_the_scenes_own_title_and_equations():
    from scholarmotion.agents.code_generator import _fallback_scene_code

    spec = SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Ohm's law",
        learning_objective="Relate voltage, current and resistance.",
        narration="Ohm's law says V equals I times R. A narrow pipe resists the flow.",
        duration_target_seconds=8,
        equations=["V = I R"],
        visual_beats=[VisualBeat(beat_id="b1", narration_segment="Ohm's law", visual="pipe")],
    )
    code = _fallback_scene_code(spec, "SceneS01", sentence_durations=[4.0, 4.0])
    assert '"Ohm\'s law"' in code and '"V = I R"' in code
    # Nothing topic-specific is invented, and the voice-over never appears on screen.
    assert "pipe" not in code.casefold()
    assert "narrow pipe resists" not in code


@pytest.mark.asyncio
async def test_a_scene_edit_shows_the_learners_request_to_the_code_model() -> None:
    class CapturingProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def generate_code(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"

    provider = CapturingProvider()
    await generate_scene_code(provider, _scene("S01"), edit_request="The figure is cut off.")
    await generate_scene_code(provider, _scene("S01"))

    assert "LEARNER'S EDIT REQUEST" in provider.prompts[0]
    assert "The figure is cut off." in provider.prompts[0]
    assert "LEARNER'S EDIT REQUEST" not in provider.prompts[1]
