"""Checks that required equations appear and are mathematically equivalent."""

from __future__ import annotations

import ast
import re

from sympy import Function, Symbol, SympifyError, simplify, sympify

from scholarmotion.schemas import VerificationIssue


def _notation_key(value: str) -> str:
    from scholarmotion.media.math_notation import canonical_to_tex

    value = canonical_to_tex(value)
    value = re.sub(r"\\(?:text|mathrm|mathsf|operatorname)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"([_^])\{([A-Za-z0-9]+)\}", r"\1\2", value)
    value = re.sub(r"\\leq?\b", "<=", value)
    value = re.sub(r"\\geq?\b", ">=", value)
    value = value.replace(r"\infty", "infinity")
    value = value.replace(r"\left", "").replace(r"\right", "")
    return "".join(value.split())


def _equation_structure(value: str):
    """Compare ordinary equation syntax without confusing TeX layout with omission.

    This preserves grouping and term order: it is not a substring test or an
    assumption that arbitrary expressions are mathematically equivalent.
    """
    from scholarmotion.media.math_notation import canonical_to_tex

    value = canonical_to_tex(value)
    value = re.sub(r"\\(?:text|mathrm|mathsf|operatorname)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"([_^])\{([A-Za-z0-9]+)\}", r"\1\2", value)
    value = value.replace(r"\left", "").replace(r"\right", "")
    for _ in range(12):
        previous = value
        value = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", value)
        value = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", value)
        if value == previous:
            break
    value = value.replace(r"\over", "/").replace(r"\cdot", "*").replace(r"\times", "*")
    value = "".join(value.split()).replace("{", "(").replace("}", ")").replace("^", "**")
    # sympify evaluates Python syntax internally. Restrict input to arithmetic
    # and bind every identifier; disallow attributes, strings and dunder names.
    if value.count("=") != 1 or not re.fullmatch(r"[A-Za-z0-9_+*/().,=\-]+", value):
        return None
    if "__" in value or re.search(r"\.[A-Za-z_]", value):
        return None
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value))
    functions = {"sqrt", "softmax", "exp", "sin", "cos", "log"}
    if any(name not in functions for name in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\(", value)):
        return None
    bindings = {
        name: Function(name) if name in functions else Symbol(name, commutative=False)
        for name in names
    }
    try:
        return tuple(sympify(part, locals=bindings, evaluate=False) for part in value.split("="))
    except (SympifyError, SyntaxError, TypeError, ValueError):
        return None


_TEX_MATRIX = re.compile(
    r"\\begin\{(?:p|b|B|v|V)?matrix\}(.*?)\\end\{(?:p|b|B|v|V)?matrix\}", re.DOTALL
)
_CODE_MATRICES = {"Matrix", "MobjectMatrix", "IntegerMatrix", "DecimalMatrix"}


def _cell(value) -> str:
    return _notation_key(str(value)).replace("\\", "")


def _tex_matrices(equation: str) -> list[tuple[tuple[str, ...], ...]]:
    """Entry grids of every matrix environment written in a required equation."""
    grids = []
    for body in _TEX_MATRIX.findall(equation):
        rows = [row for row in re.split(r"\\\\", body) if row.strip()]
        grids.append(tuple(tuple(_cell(cell) for cell in row.split("&")) for row in rows))
    return grids


def _code_matrices(tree: ast.AST) -> list[tuple[tuple[str, ...], ...]]:
    """Entry grids of Matrix(...) calls built from literal rows in scene code."""
    grids = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _CODE_MATRICES
            and node.args
            and isinstance(node.args[0], (ast.List, ast.Tuple))
        ):
            continue
        try:
            rows = ast.literal_eval(node.args[0])
            grids.append(tuple(tuple(_cell(cell) for cell in row) for row in rows))
        except (ValueError, TypeError, SyntaxError):
            continue
    return grids


def verify_equivalence(left: str, right: str) -> bool:
    try:
        return simplify(sympify(left) - sympify(right)) == 0
    except (SyntaxError, TypeError, ValueError):
        return False


def verify_equations(scene_id: str, checks: list[tuple[str, str]]) -> list[VerificationIssue]:
    return [
        VerificationIssue(
            scene_id=scene_id,
            category="math.not_equivalent",
            severity="critical",
            confidence=0.99,
            objects=[left, right],
            description=f"{left} is not equivalent to {right}.",
            suggested_repair="Revise the SceneSpec equation from a sourced derivation.",
            correction_memory_candidate=False,
        )
        for left, right in checks
        if not verify_equivalence(left, right)
    ]


def verify_required_equations(
    scene_id: str, equations: list[str], code: str
) -> list[VerificationIssue]:
    """Deterministically catch an omitted required equation before visual review."""
    # When TeX is unavailable, generated scene code contains the same formula
    # rendered as Unicode Text. Accept that normalized display form too; the
    # locked SceneSpec equation remains unchanged and is still the source of
    # truth for math checks.
    from scholarmotion.agents.code_generator import _latex_to_unicode
    from scholarmotion.media.math_notation import ascii_math, canonical_to_tex

    try:
        tree = ast.parse(code)
        displayed_strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        math_expressions = [
            "".join(arg.value for arg in node.args)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"MathTex", "Tex", "STEMEquation", "EquationPanel"}
            and node.args
            and all(
                isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args
            )
        ]
        displayed_matrices = _code_matrices(tree)
    except SyntaxError:
        displayed_strings = []
        math_expressions = []
        displayed_matrices = []

    def present(equation: str) -> bool:
        # A required matrix may be drawn with Manim's Matrix instead of a TeX
        # pmatrix string; accept it only when every entry matches cell by cell.
        required_matrices = _tex_matrices(equation)
        if required_matrices and all(grid in displayed_matrices for grid in required_matrices):
            return True
        unicode_form = _latex_to_unicode(equation)
        variants = (equation, unicode_form, ascii_math(unicode_form), canonical_to_tex(equation))
        literal_forms = {
            "".join(value.split()) for value in [*displayed_strings, *math_expressions]
        }
        exact = any("".join(form.split()) in literal_forms for form in variants)
        if exact:
            return True
        # A piecewise mask definition can legitimately be displayed on two
        # separate lines. Every required branch must still be present.
        clauses = _notation_key(equation).split(";")
        displayed_clauses = {
            part for expression in math_expressions for part in _notation_key(expression).split(";")
        }
        if all(clause in displayed_clauses for clause in clauses):
            return True
        expected = _equation_structure(equation)
        return expected is not None and any(
            expected == _equation_structure(expression) for expression in math_expressions
        )

    return [
        VerificationIssue(
            scene_id=scene_id,
            category="math.missing_required_equation",
            severity="high",
            confidence=0.98,
            objects=[equation],
            description=f"Required equation {equation!r} is absent from generated scene code.",
            suggested_repair="Render the required sourced equation with MathTex or EquationPanel.",
            correction_memory_candidate=True,
        )
        for equation in equations
        if not present(equation)
    ]
