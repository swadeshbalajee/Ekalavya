"""Verified Manim Community scene templates the code model starts from.

Each template renders cleanly and passes the layout verifier. It shows the
house layout system (named regions, role font sizes), CueClock narration
timing, persistent objects that are moved or transformed in place, and
temporaries cleared in the same ``play`` that introduces the next visual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIRECTORY = Path(__file__).parent


@dataclass(frozen=True)
class Template:
    name: str
    purpose: str
    keywords: tuple[str, ...]

    @property
    def source(self) -> str:
        return _read(self.name)


TEMPLATES = (
    Template(
        "paper_figure",
        "the paper's own figure on screen, with parts highlighted as the narration explains them",
        ("figure", "architecture", "diagram from the paper", "original figure"),
    ),
    Template(
        "data_plot",
        "a results chart from a paper: bars or lines with exact values, then the key comparison",
        (
            "bar chart",
            "chart",
            "results",
            "table",
            "benchmark",
            "accuracy",
            "bleu",
            "baseline",
            "outperform",
            "experiment",
            "ablation",
            "speedup",
            "error rate",
        ),
    ),
    Template(
        "matrix_steps",
        "matrices, vectors, tables or element-wise operations shown step by step",
        (
            "matrix",
            "matrices",
            "vector",
            "attention",
            "softmax",
            "mask",
            "query",
            "key",
            "transpose",
            "dot product",
            "linear algebra",
            "table",
            "score",
            "weights",
        ),
    ),
    Template(
        "grid_sweep",
        "a window or pointer moving over a grid/array of values (convolution, sorting, tokens)",
        (
            "grid",
            "kernel",
            "convolution",
            "pixel",
            "image",
            "array",
            "sliding",
            "window",
            "token",
            "sequence",
            "stride",
            "sort",
            "search",
        ),
    ),
    Template(
        "equation_steps",
        "a formula built or derived step by step beside a plot of what it means",
        (
            "derive",
            "derivation",
            "formula",
            "equation",
            "graph",
            "plot",
            "function",
            "curve",
            "rate",
            "derivative",
            "integral",
            "slope",
            "proportional",
        ),
    ),
    Template(
        "labeled_diagram",
        "a physical setup or mechanism with labelled parts and a quantity that changes",
        (
            "force",
            "field",
            "circuit",
            "current",
            "torque",
            "wave",
            "lens",
            "orbit",
            "pressure",
            "charge",
            "magnet",
            "pendulum",
            "mechanism",
            "diagram",
        ),
    ),
)


@cache
def _read(name: str) -> str:
    return (_DIRECTORY / f"{name}.py").read_text(encoding="utf-8")


def choose_template(
    text: str, *, has_data_plot: bool = False, has_paper_figure: bool = False
) -> Template:
    """Pick the template whose visual vocabulary best matches the scene plan."""
    by_name = {template.name: template for template in TEMPLATES}
    if has_paper_figure:
        return by_name["paper_figure"]
    if has_data_plot:
        return by_name["data_plot"]
    lowered = text.casefold()
    scores = [
        (sum(len(re.findall(rf"\b{re.escape(word)}", lowered)) for word in item.keywords), -index)
        for index, item in enumerate(TEMPLATES)
    ]
    best = max(range(len(TEMPLATES)), key=lambda index: scores[index])
    return TEMPLATES[best] if scores[best][0] else TEMPLATES[-1]


def with_cues(code: str, durations: list[float]) -> str:
    """Write the measured narration cue durations into a module-level CUES list."""
    if not durations:
        return code
    values = ", ".join(f"{max(0.2, value):.2f}" for value in durations)
    return re.sub(
        r"(?m)^(\s*)CUES\s*=\s*(?:(?:np\.)?(?:array|list|tuple)\(\s*)?[\[(][^\])]*[\])]\)?",
        rf"\1CUES = [{values}]",
        code,
        count=1,
    )
