"""How much each model may think before it answers.

Several open models ignore OpenRouter's ``effort`` levels: asked for "low",
they still spend the whole output budget reasoning and never write the answer.
A rule per model fixes that, either a token cap on thinking or thinking off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thinking:
    """``budget`` caps thinking tokens; ``off`` disables thinking; else ``effort``."""

    effort: str | None = None
    budget: int | None = None
    off: bool = False

    def reasoning(self) -> dict | None:
        """OpenRouter's ``reasoning`` request field for this rule."""
        if self.off:
            return {"effort": "none"}
        if self.budget:
            return {"max_tokens": self.budget}
        return {"effort": self.effort} if self.effort else None

    def output_tokens(self, answer_tokens: int) -> int:
        """Output limit that leaves ``answer_tokens`` for the answer after thinking."""
        return answer_tokens + (self.budget or 0)


def parse_rules(value: str | None) -> list[tuple[str, str]]:
    """``"model=6000,prefix/=off"`` as (model or prefix, rule) pairs, in order."""
    rules = []
    for part in (value or "").split(","):
        model, _, rule = part.strip().partition("=")
        if model.strip() and rule.strip():
            rules.append((model.strip(), rule.strip().lower()))
    return rules


def thinking_for(model: str, effort: str | None, rules: list[tuple[str, str]]) -> Thinking:
    """The rule for ``model``: an exact match first, then the longest matching prefix."""
    exact = [rule for name, rule in rules if name == model]
    prefixes = sorted(
        ((name, rule) for name, rule in rules if name.endswith("/") and model.startswith(name)),
        key=lambda item: -len(item[0]),
    )
    rule = exact[0] if exact else (prefixes[0][1] if prefixes else None)
    if rule in {"off", "none"}:
        return Thinking(off=True)
    if rule and rule.isdigit():
        return Thinking(budget=int(rule))
    if rule in {"low", "medium", "high"}:
        return Thinking(effort=rule)
    return Thinking(effort=effort)
