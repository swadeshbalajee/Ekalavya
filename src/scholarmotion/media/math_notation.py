"""Canonical notation to TeX, without depending on a font's Unicode math coverage."""

from __future__ import annotations

import re
import unicodedata

_SCRIPTS = {
    "ᵀ": r"^{\mathsf{T}}",
    "ₖ": "_{k}",
    "₀": "_{0}",
    "₁": "_{1}",
    "₂": "_{2}",
    "²": "^{2}",
    "³": "^{3}",
    "⁻": "^{-}",
    "⁵": "^{5}",
}


def needs_math_typesetting(value: str) -> bool:
    return bool(re.search(r"[=^_∇∂]|[ᵀₖ₀₁₂²³⁻⁵]", value))


def contains_native_prose(value: str) -> bool:
    return any(
        char.isalpha()
        and char not in _SCRIPTS
        and not "\u2070" <= char <= "\u209f"
        and not unicodedata.name(char, "").startswith(("LATIN", "GREEK", "MATHEMATICAL"))
        for char in value
    )


def canonical_to_tex(value: str) -> str:
    value = value.strip().strip("$").replace(r"\(", "").replace(r"\)", "")
    value = re.sub(r"(?<![A-Za-z])(?:-infinity|-inf)\b", lambda _: r"-\infty ", value)
    value = value.replace("<=", r"\leq ").replace(">=", r"\geq ")
    superscripts = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    value = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^{" + m[0].translate(superscripts) + "}", value)
    for char, tex in _SCRIPTS.items():
        value = value.replace(char, tex)
    for char, tex in {
        "μ": r"\mu ",
        "α": r"\alpha ",
        "β": r"\beta ",
        "γ": r"\gamma ",
        "δ": r"\delta ",
        "λ": r"\lambda ",
        "σ": r"\sigma ",
        "ω": r"\omega ",
        "Δ": r"\Delta ",
        "Σ": r"\Sigma ",
        "Ω": r"\Omega ",
        "ε": r"\epsilon ",
        "π": r"\pi ",
        "ρ": r"\rho ",
        "θ": r"\theta ",
        "Φ": r"\Phi ",
        "τ": r"\tau ",
        "∇": r"\nabla ",
        "∂": r"\partial ",
        "∞": r"\infty ",
        "×": r"\times ",
        "·": r"\cdot ",
        "≈": r"\approx ",
        "≤": r"\leq ",
        "≥": r"\geq ",
        "≠": r"\neq ",
        "∝": r"\propto ",
        "→": r"\to ",
        "−": "-",
    }.items():
        value = value.replace(char, tex)
    value = value.replace("*", r"\cdot ")
    value = re.sub(r"_([A-Za-z0-9]+)", lambda m: "_{" + m[1] + "}", value)
    value = re.sub(r"\^T\b", lambda _: r"^{\mathsf{T}}", value)
    value = re.sub(r"√([A-Za-z](?:_\{[^}]+\})?)", lambda m: r"\sqrt{" + m[1] + "}", value)
    # Fold innermost square roots first; repeated passes handle nesting.
    for _ in range(8):
        updated = re.sub(r"sqrt\(([^()]*)\)", lambda m: r"\sqrt{" + m[1] + "}", value)
        if updated == value:
            break
        value = updated
    for name in ("Attention", "softmax"):
        value = re.sub(
            rf"(?<![A-Za-z\\{{]){name}\b", lambda m: r"\operatorname{" + m[0] + "}", value
        )
    return value


def ascii_math(value: str) -> str:
    for char, replacement in {
        "ᵀ": " transpose",
        "ₖ": "_k",
        "₀": "_0",
        "₁": "_1",
        "₂": "_2",
        "²": "^2",
        "³": "^3",
        "⁻": "-",
        "⁵": "5",
    }.items():
        value = value.replace(char, replacement)
    return value
