"""Check that a scene shows the paper figures it was given."""

from __future__ import annotations

import ast

from scholarmotion.schemas import SceneSpec, VerificationIssue


def _shown_figures(python_code: str) -> set[str] | None:
    """Filenames passed to paper_figure(...), or None when the code does not parse."""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return None
    shown = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "paper_figure"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            shown.add(node.args[0].value)
    return shown


def verify_paper_figures_shown(spec: SceneSpec, python_code: str) -> list[VerificationIssue]:
    """A scene given a paper figure must show it, not leave it out or redraw it."""
    if not spec.paper_figures:
        return []
    shown = _shown_figures(python_code)
    if shown is None:  # the render layer reports code that does not parse
        return []
    return [
        VerificationIssue(
            scene_id=spec.scene_id,
            category="figure.missing",
            severity="high",
            confidence=1,
            objects=[figure.label],
            description=f"The scene never shows {figure.label} from the paper.",
            suggested_repair=(
                f"Show {figure.label} with paper_figure({figure.filename!r}, 'main') while the "
                "narration discusses it, and point at its parts; never redraw it."
            ),
            correction_memory_candidate=False,
        )
        for figure in spec.paper_figures
        if figure.filename not in shown
    ]
