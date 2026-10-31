import json

from pydantic import BaseModel

from scholarmotion.providers.llm.base import restore_latex_escapes


class _Beat(BaseModel):
    visual: str
    tags: list[str] = []


class _Plan(BaseModel):
    summary: str
    beats: list[_Beat] = []


def test_unescaped_latex_in_model_json_is_restored():
    # What a model sends when it forgets to double the backslashes.
    raw = '{"summary": "$u\\text{ cos}\\theta$ and $\\frac{1}{2}$ with $\\beta \\times \\rho$"}'
    decoded = _Plan.model_validate(json.loads(raw))
    assert "\t" in decoded.summary and "\f" in decoded.summary

    restored = restore_latex_escapes(decoded)

    assert restored.summary == r"$u\text{ cos}\theta$ and $\frac{1}{2}$ with $\beta \times \rho$"


def test_nested_lists_and_models_are_restored():
    plan = _Plan(summary="ok", beats=[_Beat(visual="$m = 9.11 \times 10^{-31}$", tags=["\text"])])

    restore_latex_escapes(plan)

    assert plan.beats[0].visual == r"$m = 9.11 \times 10^{-31}$"
    assert plan.beats[0].tags == [r"\text"]


def test_real_tabs_and_line_breaks_are_kept():
    text = "Step 1\tdone\nnot in math\nnumbers stay\t42"

    assert restore_latex_escapes(text) == text


def test_newline_is_restored_only_inside_math():
    text = "A line\nnot math, but $a \neq b$ and $\nabla f$"

    assert restore_latex_escapes(text) == "A line\nnot math, but $a \\neq b$ and $\\nabla f$"


def test_correct_latex_is_unchanged():
    text = r"$\text{W}_1 \times \frac{a}{b}$"

    assert restore_latex_escapes(text) == text
