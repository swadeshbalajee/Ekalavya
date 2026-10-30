"""Turn written STEM narration into words a speech engine reads correctly.

The script writer is asked for spoken mathematics, but models still leak
notation such as ``Q*K``, ``QKᵀ``, ``d_k`` or ``\\frac{a}{b}``. A TTS engine
pronounces those characters literally ("Q asterisk K"), so every narration is
normalized here immediately before synthesis and subtitles. Rules only touch
notation; ordinary prose ("and/or", "e.g.", hyphenated words) must survive.
"""

from __future__ import annotations

import re

_GREEK = {
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
    "Gamma": "gamma",
    "delta": "delta",
    "Delta": "delta",
    "epsilon": "epsilon",
    "varepsilon": "epsilon",
    "zeta": "zeta",
    "eta": "eta",
    "theta": "theta",
    "Theta": "theta",
    "vartheta": "theta",
    "iota": "iota",
    "kappa": "kappa",
    "lambda": "lambda",
    "Lambda": "lambda",
    "mu": "mu",
    "nu": "nu",
    "xi": "xi",
    "Xi": "xi",
    "pi": "pi",
    "Pi": "pi",
    "rho": "rho",
    "sigma": "sigma",
    "Sigma": "sigma",
    "tau": "tau",
    "upsilon": "upsilon",
    "phi": "phi",
    "Phi": "phi",
    "varphi": "phi",
    "chi": "chi",
    "psi": "psi",
    "Psi": "psi",
    "omega": "omega",
    "Omega": "omega",
    "hbar": "h bar",
    "ell": "l",
}

_UNICODE_SYMBOLS = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "Γ": "gamma",
    "δ": "delta",
    "Δ": "delta",
    "ε": "epsilon",
    "ϵ": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ϑ": "theta",
    "κ": "kappa",
    "λ": "lambda",
    "Λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "Σ": "sigma",
    "τ": "tau",
    "φ": "phi",
    "ϕ": "phi",
    "Φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "Ψ": "psi",
    "ω": "omega",
    "Ω": "omega",
    "ħ": "h bar",
    "≤": " is less than or equal to ",
    "≥": " is greater than or equal to ",
    "≠": " is not equal to ",
    "≈": " is approximately ",
    "≃": " is approximately ",
    "∝": " is proportional to ",
    "→": " goes to ",
    "⇒": " implies ",
    "↦": " maps to ",
    "∈": " in ",
    "±": " plus or minus ",
    "∓": " minus or plus ",
    "÷": " divided by ",
    "°": " degrees",
    "∑": "the sum of ",
    "∫": "the integral of ",
    "∏": "the product of ",
    "∂": "partial ",
    "∞": "infinity",
}

_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉ₐₑᵢⱼₖₗₘₙₒₚₛₜᵤₓ", "0123456789aeijklmnopstux")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ", "0123456789+-ni")

_UNITS = {
    "m/s^2": "meters per second squared",
    "m/s²": "meters per second squared",
    "m/s": "meters per second",
    "km/h": "kilometers per hour",
    "km/s": "kilometers per second",
    "N/m": "newtons per meter",
    "N/C": "newtons per coulomb",
    "V/m": "volts per meter",
    "J/K": "joules per kelvin",
    "kg/m^3": "kilograms per cubic meter",
    "kg/m³": "kilograms per cubic meter",
    "mol/L": "moles per liter",
}

# Captured operand on the left of an operator. Long words are prose, except the
# spoken endings this module itself produces ("K transpose / ...").
_OPERAND = r"(\b\d+(?:\.\d+)?|\b[A-Za-z]{1,2}\b|transpose|squared|cubed|inverse|[)\]])"
# Stricter operand for minus and comparisons, where prose dashes are common.
_SYMBOL = r"(\b\d+(?:\.\d+)?|\b[A-Za-z]\b|transpose|squared|cubed|infinity|[)\]])"


def _superscript(match: re.Match) -> str:
    exponent = match.group(1).strip()
    if exponent in {"T", r"\top", r"\mathsf{T}", r"\mathrm{T}", "⊤"}:
        return " transpose"
    if exponent == "2":
        return " squared"
    if exponent == "3":
        return " cubed"
    if exponent in {"-1", "−1"}:
        return " inverse"
    if exponent.startswith(("-", "−")):
        return f" to the negative {exponent[1:]}"
    if re.fullmatch(r"\d+", exponent):
        return f" to the power {exponent}"
    return f" to the power {exponent}"


def to_speakable(text: str) -> str:
    if not text:
        return ""
    value = text.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")
    value = value.replace("$", "")
    # Markdown emphasis is never spoken.
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    for written, spoken in {
        "e.g.": "for example",
        "i.e.": "that is",
        "vs.": "versus",
        "etc.": "and so on",
    }.items():
        value = value.replace(written, spoken)
    for unit, spoken in _UNITS.items():
        value = re.sub(rf"(?<=\d)\s*{re.escape(unit)}(?![\w/^])", f" {spoken}", value)

    value = re.sub(
        r"\\(?:operatorname|mathrm|mathsf|mathbf|mathit|text|textbf|boldsymbol)\{([^{}]+)\}",
        r"\1",
        value,
    )
    value = re.sub(r"\\(?:hat|bar|tilde|vec|dot)\{([^{}]+)\}", r"\1", value)
    # Expand inner expressions first, so fractions containing roots (and vice
    # versa) do not leak command names into the synthesized speech.
    for _ in range(12):
        previous = value
        value = re.sub(r"\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}", r"\1 over \2", value)
        value = re.sub(r"\\sqrt\{([^{}]+)\}", r"square root of \1", value)
        value = re.sub(r"\\sqrt\[(\d)\]\{([^{}]+)\}", r"root \1 of \2", value)
        if value == previous:
            break
    value = re.sub(r"(?:∇|\\nabla)\s*(?:·|\\cdot)", "divergence of ", value)
    value = re.sub(r"(?:∇|\\nabla)\s*(?:×|\\times)", "curl of ", value)
    value = re.sub(r"(?:∇|\\nabla)\s*", "gradient of ", value)
    value = re.sub(r"\\sum_\{([^{}]+)\}\^\{([^{}]+)\}", r"the sum from \1 to \2 of ", value)
    value = re.sub(
        r"\\int_\{?([^{}\s^]+)\}?\^\{?([^{}\s]+)\}?", r"the integral from \1 to \2 of ", value
    )
    value = re.sub(r"\\epsilon_0|\\varepsilon_0|ε₀|ε_0", "epsilon naught", value)
    value = re.sub(r"\\mu_0|μ₀|μ_0", "mu naught", value)
    for command, spoken in {
        "cdot": " dot ",
        "times": " times ",
        "div": " divided by ",
        "pm": " plus or minus ",
        "approx": " is approximately ",
        "propto": " is proportional to ",
        "leq": " is less than or equal to ",
        "le": " is less than or equal to ",
        "geq": " is greater than or equal to ",
        "ge": " is greater than or equal to ",
        "neq": " is not equal to ",
        "to": " goes to ",
        "rightarrow": " goes to ",
        "infty": "infinity",
        "partial": "partial ",
        "sum": "the sum of ",
        "int": "the integral of ",
        "log": " log ",
        "ln": " natural log of ",
        "sin": " sine ",
        "cos": " cosine ",
        "tan": " tangent ",
        "exp": " exp ",
        "max": " max ",
        "min": " min ",
        "left": "",
        "right": "",
        "quad": " ",
        ",": " ",
        **{name: f" {word} " for name, word in _GREEK.items()},
    }.items():
        value = re.sub(
            r"\\" + re.escape(command) + r"(?![A-Za-z])", lambda _, word=spoken: word, value
        )

    # Derivatives before generic division: dy/dx -> d y by d x.
    value = re.sub(r"\bd([A-Za-z])\s*/\s*d([A-Za-z])\b", r"d \1 by d \2", value)
    value = re.sub(r"∂([A-Za-z])\s*/\s*∂([A-Za-z])", r"partial \1 by partial \2", value)

    # Implicit products of capital matrices before transpose: QKᵀ / QK^T.
    value = re.sub(
        r"\b([A-Z])([A-Z])(?=\^\{?(?:T|\\top|\\mathsf\{T\})\}?|ᵀ|⊤)", r"\1 times \2", value
    )
    value = re.sub(r"[ᵀ⊤]", " transpose", value)
    value = re.sub(r"\^\{?(?:T|\\top|\\mathsf\{T\})\}?(?![A-Za-z])", " transpose", value)
    # Unicode scripts: dₖ -> d_k, x² -> x^2.
    value = re.sub(
        r"[₀₁₂₃₄₅₆₇₈₉ₐₑᵢⱼₖₗₘₙₒₚₛₜᵤₓ]+", lambda m: "_" + m[0].translate(_SUBSCRIPTS), value
    )
    value = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ]+", lambda m: "^" + m[0].translate(_SUPERSCRIPTS), value)

    value = re.sub(r"sqrt\s*\(([^()]*)\)", r"square root of \1", value)
    value = re.sub(r"√\s*\(([^()]*)\)", r"square root of \1", value)
    value = re.sub(r"√\s*([A-Za-z0-9]+(?:_\{?[A-Za-z0-9]+\}?)?)", r"square root of \1", value)
    # Subscripts: d_k -> d sub k, x_0 -> x naught, a_{ij} -> a sub i j.
    value = re.sub(
        r"(?<=[A-Za-z])_\{?([A-Za-z0-9]+)\}?",
        lambda m: (
            " naught"
            if m[1] == "0"
            else " sub " + (" ".join(m[1]) if len(m[1]) == 2 and m[1].isalpha() else m[1])
        ),
        value,
    )
    # Powers: r^2 -> r squared, x^{n+1} -> x to the power n+1, A^T handled above.
    value = re.sub(r"\^\{([^{}]+)\}", _superscript, value)
    value = re.sub(r"\^\s*(-?\w+)", _superscript, value)

    # Multiplication: Q*K, 2 * x, (a)*(b). A lone asterisk is never spoken.
    value = re.sub(r"(?<=[\w)\]])\s*\*\s*(?=[\w(\[])", " times ", value)
    value = value.replace("*", "")

    for fraction, spoken in {
        "1/2": "one half",
        "1/3": "one third",
        "2/3": "two thirds",
        "1/4": "one quarter",
        "3/4": "three quarters",
    }.items():
        value = re.sub(rf"(?<![\w.]){re.escape(fraction)}(?![\w.])", spoken, value)
    # Operators between symbolic operands only; prose such as "and/or" stays.
    value = re.sub(
        rf"{_OPERAND}\s*/\s*(?=\(|\d|[A-Za-z]{{1,2}}\b|square root)", r"\1 divided by ", value
    )
    value = re.sub(r"(?<=\w)/(?=\w)", " or ", value)
    value = re.sub(r"\s*<=\s*", " is less than or equal to ", value)
    value = re.sub(r"\s*>=\s*", " is greater than or equal to ", value)
    value = re.sub(r"\s*(?<![<>!=])=(?!=)\s*", " equals ", value)
    value = re.sub(rf"{_SYMBOL}\s*<\s*(?={_SYMBOL})", r"\1 is less than ", value)
    value = re.sub(rf"{_SYMBOL}\s*>\s*(?={_SYMBOL})", r"\1 is greater than ", value)
    value = re.sub(r"\s*\+\s*", " plus ", value)
    value = value.replace("−∞", "negative infinity").replace("-∞", "negative infinity")
    value = value.replace("-infinity", "negative infinity")
    value = re.sub(rf"{_SYMBOL}\s*−\s*(?={_SYMBOL}|\()", r"\1 minus ", value)
    value = re.sub(rf"{_SYMBOL}\s+-\s+(?={_SYMBOL}|\()", r"\1 minus ", value)
    value = value.replace("−", "-")
    value = re.sub(r"(?<=\d)\s*%", " percent", value)
    for symbol, spoken in _UNICODE_SYMBOLS.items():
        value = value.replace(symbol, spoken)
    value = value.replace("×", " times ").replace("·", " dot ")
    value = re.sub(r"\\[A-Za-z]+", "", value)
    value = re.sub(r"[{}\\^_|]", " ", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()
