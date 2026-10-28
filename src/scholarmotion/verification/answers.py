"""Deterministic checks on tutor chat replies: arithmetic and source citations.

No model judges the reply. Worked examples are checked by evaluating every
equation in the reply whose sides are pure numbers (``$\\frac{1}{2}\\cdot 4
\\cdot 3^2 = 18$``); equations with symbols (``F = ma``) are definitions and are
skipped. Citations such as ``[paper, p. 3]`` must name a page that was among
the retrieved excerpts, and the numbers in the citing sentence must appear in
that excerpt.
"""

from __future__ import annotations

import itertools
import re
from decimal import Decimal

from sympy import N, sympify

_MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\[(.+?)\\\]|\\\((.+?)\\\)", re.DOTALL)
# Plain-text arithmetic such as "3 × 4 = 12" outside any math markup.
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_PLAIN = re.compile(
    rf"(?<![\w.$])((?:{_NUMBER}\s*[-+×x*/÷^]\s*)+{_NUMBER}\s*(?:=|≈)\s*{_NUMBER})(?!\w)(?!\.\d)"
)
_CITATION = re.compile(r"\[([^\[\]]{1,200}?),\s*p\.\s*(\d+)[^\]]*\]")
_EXCERPT = re.compile(r"^\[(.+?), p\. (\d+|\?)(?:, [^\]]*)?\] (.*)$")
_ALLOWED = re.compile(r"[0-9+\-*/().]+")


def _to_python(tex: str) -> str | None:
    """Arithmetic in TeX or plain notation as a Python expression; None if not pure numbers."""
    value = tex
    value = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{[^{}]*\}", "", value)  # units
    value = value.replace(r"\left", "").replace(r"\right", "").replace(r"\,", "")
    for _ in range(8):
        previous = value
        value = re.sub(r"\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", value)
        value = re.sub(r"\\sqrt\{([^{}]+)\}", r"((\1)**0.5)", value)
        if value == previous:
            break
    for written, python in {
        r"\times": "*",
        r"\cdot": "*",
        r"\div": "/",
        "×": "*",
        "·": "*",
        "÷": "/",
        "−": "-",
        "^": "**",
        "{": "(",
        "}": ")",
    }.items():
        value = value.replace(written, python)
    value = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", value)  # thousands separators
    value = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", value)  # "3 x 4"
    value = "".join(value.split())
    if not value or not _ALLOWED.fullmatch(value) or not re.search(r"\d", value):
        return None
    return value


def _decimals(text: str) -> int:
    match = re.search(r"\.(\d+)\s*$", text.strip())
    return len(match.group(1)) if match else 0


def _check_chain(expression: str) -> str | None:
    """A problem with an ``a = b = c`` chain of numbers, or None when it holds."""
    parts = re.split(r"(=|≈|\\approx)", expression)
    sides = parts[0::2]
    relations = parts[1::2]
    if len(sides) < 2:
        return None
    values = []
    for side in sides:
        python = _to_python(side)
        if python is None:
            return None  # not pure arithmetic: a definition or symbolic identity
        try:
            values.append(float(N(sympify(python), 15)))
        except (TypeError, ValueError, ZeroDivisionError, SyntaxError):
            return None
    for (left, right), relation, shown in zip(
        itertools.pairwise(values), relations, sides[1:], strict=True
    ):
        places = _decimals(_to_python(shown) or "")
        tolerance = max(1e-9 * max(1.0, abs(right)), 0.5 * 10**-places if places else 0.5e-9)
        if relation != "=":
            tolerance = max(tolerance, 0.02 * abs(right), 0.5 * 10**-places)
        elif places == 0 and float(right).is_integer() and not float(left).is_integer():
            # "= 7" for 6.98 is a rounding, not an error, only when ≈ is written.
            tolerance = max(tolerance, 1e-9)
        if abs(left - right) > tolerance:
            return f"{expression.strip()} is wrong: the left side equals {_format(left)}"
    return None


def _format(value: float) -> str:
    text = format(Decimal(repr(value)).normalize(), "f")
    return text.removesuffix(".0")


def arithmetic_problems(answer: str) -> list[str]:
    """Numeric equations in the reply that do not hold."""
    problems: list[str] = []
    spans = [next(group for group in match.groups() if group) for match in _MATH.finditer(answer)]
    prose = _MATH.sub(" ", answer)
    spans += [match.group(1) for match in _PLAIN.finditer(prose)]
    for span in spans:
        for line in re.split(r"\\\\|\n|&", span):
            problem = _check_chain(line)
            if problem and problem not in problems:
                problems.append(problem)
    return problems


def _excerpts(context: str) -> list[tuple[str, str, str]]:
    found = []
    for block in context.split("\n\n"):
        match = _EXCERPT.match(block.strip())
        if match:
            found.append((match.group(1).casefold(), match.group(2), match.group(3)))
    return found


def _numbers(text: str) -> set[str]:
    return {value.replace(",", "").rstrip(".") for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def citation_problems(answer: str, context: str) -> list[str]:
    """Citations to pages that were not retrieved, or whose numbers the page does not contain."""
    excerpts = _excerpts(context)
    if not excerpts:
        return []
    problems: list[str] = []
    # Split into sentences without breaking at the "p." inside a citation.
    for sentence in re.split(r"(?<=[.!?])(?<!\sp\.)(?<!\spp\.)\s+|\n+", answer):
        for match in _CITATION.finditer(sentence):
            title, page = match.group(1).casefold().strip(), match.group(2)
            pages = [
                text
                for source, number, text in excerpts
                if number == page and (title in source or source in title or title == "source")
            ]
            if not pages:
                problems.append(
                    f"{match.group(0)} cites a page that was not in the provided excerpts"
                )
                continue
            claimed = _numbers(_CITATION.sub("", sentence))
            available = set().union(*(_numbers(text) for text in pages))
            missing = sorted(claimed - available)
            if missing:
                problems.append(
                    f"{match.group(0)}: the cited page does not contain {', '.join(missing)}"
                )
    return problems


def answer_problems(answer: str, context: str = "") -> list[str]:
    """Every verified problem in a tutor reply; empty when it checks out."""
    return arithmetic_problems(answer) + citation_problems(answer, context)
