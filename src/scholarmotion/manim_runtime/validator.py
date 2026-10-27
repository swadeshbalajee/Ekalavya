"""Static AST checks on generated Manim code before it is rendered."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

_TEXT_LIKE_CONSTRUCTORS = {"Text", "SafeTitle", "TimelineLabel"}
_LATEX_MARKER = re.compile(r"\\[A-Za-z]|\$")
_MATH_LIKE_CONSTRUCTORS = {
    "MathTex",
    "Tex",
    "EquationPanel",
    "StepEquationTransform",
    "STEMEquation",
}
_UNESCAPED_LATEX_SPECIAL = re.compile(r"(?<!\\)[&%#]")

ALLOWED_IMPORT_ROOTS = {"manim", "math", "numpy", "sympy", "scholarmotion", "__future__"}
FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "socket.socket",
    "requests.get",
    "requests.post",
    "urllib.request.urlopen",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
    "shutil.rmtree",
}
FORBIDDEN_ATTRIBUTES = {"__subclasses__", "__globals__", "__code__", "__dict__"}
ANIMATION_CALLS = {"FadeIn", "FadeOut", "Write", "Create", "GrowArrow", "Indicate"}


@dataclass
class ValidationResult:
    accepted: bool
    errors: list[str] = field(default_factory=list)
    scene_classes: list[str] = field(default_factory=list)


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def validate_generated_code(code: str) -> ValidationResult:
    errors: list[str] = []
    scenes: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(False, [f"syntax error at line {exc.lineno}: {exc.msg}"])

    list_variables: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            list_variables.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.value, ast.List)
            and isinstance(node.target, ast.Name)
        ):
            list_variables.add(node.target.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    errors.append(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_IMPORT_ROOTS:
                errors.append(f"import not allowed: {node.module}")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_CALLS or name.split(".")[-1] in {
                "eval",
                "exec",
                "compile",
                "__import__",
            }:
                errors.append(f"call not allowed: {name}")
            elif name.split(".")[-1] in ANIMATION_CALLS and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.List) or (
                    isinstance(argument, ast.Name) and argument.id in list_variables
                ):
                    errors.append(
                        f"{name} cannot animate a Python list; wrap its Mobjects in VGroup(*list_name)"
                    )
            elif name == "SubtitleSafeRegion" and node.args:
                errors.append(
                    "SubtitleSafeRegion accepts keyword arguments only; call SubtitleSafeRegion()"
                )
            elif (
                name == "keep_inside_frame"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"
            ):
                errors.append(
                    "keep_inside_frame accepts a mobject, not the Scene; call keep_inside_frame(mobject)"
                )
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            errors.append(f"attribute not allowed: {node.attr}")
        elif isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id.endswith("Scene") for base in node.bases
        ):
            scenes.append(node.name)
    if not scenes:
        errors.append("no Manim Scene subclass found")
    return ValidationResult(not errors, sorted(set(errors)), scenes)


def _equation_string_constants(node: ast.Call) -> list[ast.Constant]:
    name = _call_name(node)
    constants: list[ast.Constant] = []
    if name in {"MathTex", "Tex"}:
        constants.extend(arg for arg in node.args[:1] if isinstance(arg, ast.Constant))
    elif name == "StepEquationTransform":
        constants.extend(arg for arg in node.args if isinstance(arg, ast.Constant))
    elif name == "EquationPanel":
        constants.extend(arg for arg in node.args[:1] if isinstance(arg, ast.Constant))
        for keyword in node.keywords:
            if keyword.arg == "equation" and isinstance(keyword.value, ast.Constant):
                constants.append(keyword.value)
            elif keyword.arg == "equations" and isinstance(keyword.value, ast.List):
                constants.extend(
                    item for item in keyword.value.elts if isinstance(item, ast.Constant)
                )
    return [item for item in constants if isinstance(item.value, str)]


def find_unescaped_latex_specials(code: str) -> list[str]:
    """Detect a literal &, %, or # inside MathTex/Tex/EquationPanel source, which LaTeX
    interprets as an alignment tab / comment / parameter marker and fails to compile."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for constant in _equation_string_constants(node):
            checked = re.sub(
                r"\\begin\{(cases|matrix|bmatrix|pmatrix|vmatrix|Vmatrix|aligned|alignedat|array|split)\}(.*?)\\end\{\1\}",
                lambda match: match[0].replace("&", r"\&"),
                constant.value,
                flags=re.DOTALL,
            )
            if _UNESCAPED_LATEX_SPECIAL.search(checked):
                findings.append(
                    f"{_call_name(node)}({constant.value!r}) contains an unescaped '&', '%', or "
                    "'#' — LaTeX treats these as special control characters and will fail to "
                    "compile. Escape them (\\&, \\%, \\#), or better, don't put plain English "
                    "prose in MathTex/EquationPanel at all — use Text/SafeTitle for that."
                )
    return findings


def find_raw_tex_in_text(code: str) -> list[str]:
    """Detect LaTeX source (e.g. r"\\Phi_B" or "$...$") passed to Text/SafeTitle/TimelineLabel
    instead of MathTex/EquationPanel, which renders it as literal characters, not a symbol."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in _TEXT_LIKE_CONSTRUCTORS:
            continue
        for arg in node.args[:1]:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _LATEX_MARKER.search(arg.value)
            ):
                findings.append(
                    f"{_call_name(node)}({arg.value!r}) contains raw LaTeX source; {_tex_advice()}"
                )
    return findings


def _tex_advice() -> str:
    """Repair advice that matches what can actually render here.

    Pointing at MathTex when no TeX distribution is installed sends the repair
    loop toward code that cannot compile, so it burns every attempt and the
    scene is blocked for the wrong reason.
    """
    from scholarmotion.agents.code_generator import latex_available

    if latex_available():
        return "use MathTex(...) or EquationPanel(...) instead so it renders as a real symbol."
    return (
        "LaTeX is not installed here, so MathTex/EquationPanel cannot render either; "
        "rewrite it with Unicode characters inside Text(...), e.g. Text('E ∝ 1/r³')."
    )
