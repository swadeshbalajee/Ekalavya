"""Protect what translation must not change: numbers, units, equations, STEM terms.

English source text is analysed before translation. Numbers (with their unit,
percent or power), equations and the lesson's key STEM terms are replaced by
placeholders, so no translator can alter them, and restored afterwards in the
form each copy needs: digits and ``English term (native term)`` on screen,
number words in the narration language for speech. Names of people, places and
organisations are found with spaCy's small English NER model and are left for
the translator to render, then checked (see :mod:`.checks`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import cache

from scholarmotion.translation.spoken import UNIT_NAMES, spoken_quantity

logger = logging.getLogger(__name__)

NAME_LABELS = {"PERSON", "ORG", "GPE", "LOC", "FAC", "PRODUCT", "WORK_OF_ART", "EVENT", "LAW"}
_UNIT_WORDS = sorted({*UNIT_NAMES, *UNIT_NAMES.values()}, key=len, reverse=True)
_NUMBER = re.compile(
    r"(?<![\w.\]])"
    r"(?P<num>[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:\^|\*\*)\s*(?P<pow>-?\d+)|(?P<sup>[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+))?"
    r"(?:\s*(?P<pct>%|per ?cent\b)"
    r"|\s*(?P<unit>" + "|".join(re.escape(unit) for unit in _UNIT_WORDS) + r")(?![\w²³]))?"
)
TOKEN = re.compile(r"\[\[ZQ[A-Z]+\]\]")


def _token(index: int) -> str:
    """``[[ZQA]]``, ``[[ZQB]]`` … letters only, so no translator re-writes digits."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return f"[[ZQ{letters}]]"


@dataclass(frozen=True)
class Protected:
    token: str
    kind: str  # number | year | percent | quantity | term | equation
    text: str  # the original English text the token replaced
    number: str = ""
    unit: str | None = None


@dataclass
class MaskedText:
    source: str
    text: str
    items: list[Protected] = field(default_factory=list)
    names: list[str] = field(default_factory=list)

    def item(self, token: str) -> Protected | None:
        return next((item for item in self.items if item.token == token), None)


@cache
def _nlp():
    """spaCy's small English pipeline, or None when it is not installed."""
    try:
        import spacy

        return spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
    except (ImportError, OSError) as error:
        logger.warning("spaCy en_core_web_sm unavailable; name checks are skipped: %s", error)
        return None


def _entities(text: str) -> list[tuple[str, str, int, int]]:
    nlp = _nlp()
    if nlp is None or not text.strip():
        return []
    return [(ent.text, ent.label_, ent.start_char, ent.end_char) for ent in nlp(text).ents]


def mask(
    text: str,
    *,
    key_terms: tuple[str, ...] | list[str] = (),
    equations: tuple[str, ...] | list[str] = (),
) -> MaskedText:
    """Replace protected spans of English ``text`` with placeholders."""
    entities = _entities(text)
    date_spans = [(start, end) for _, label, start, end in entities if label == "DATE"]
    items: list[Protected] = []

    def protect(kind: str, original: str, **extra) -> str:
        item = Protected(_token(len(items)), kind, original, **extra)
        items.append(item)
        return item.token

    masked = text
    for equation in sorted({e for e in equations if e}, key=len, reverse=True):
        if equation in masked:
            masked = masked.replace(equation, protect("equation", equation))
    for term in sorted({t.strip() for t in key_terms if t.strip()}, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        masked = pattern.sub(lambda match: protect("term", match.group(0)), masked)

    def number(match: re.Match) -> str:
        value = match.group("num")
        power = match.group("pow") or (match.group("sup") or "")
        unit = match.group("unit")
        if match.group("pct"):
            kind = "percent"
        elif unit:
            kind = "quantity"
        elif _is_year(value, date_spans, text):
            kind = "year"
        else:
            kind = "number"
        shown = value + (
            f"^{match.group('pow')}" if match.group("pow") else match.group("sup") or ""
        )
        return protect(kind, match.group(0), number=shown if power else value, unit=unit)

    masked = _NUMBER.sub(number, masked)
    taken = {item.text.casefold() for item in items}
    names = [
        name
        for name, label, _, _ in entities
        if label in NAME_LABELS and name.casefold() not in taken and name in masked
    ]
    return MaskedText(text, masked, items, list(dict.fromkeys(names)))


def _is_year(value: str, date_spans: list[tuple[int, int]], source: str) -> bool:
    """A 4-digit number NER tagged as a date; without NER, any value in 1000–2100."""
    if not re.fullmatch(r"\d{4}", value) or not 1000 <= int(value) <= 2100:
        return False
    if _nlp() is None:
        return True
    return any(value in source[start:end] for start, end in date_spans)


def restore_screen(translated: str, masked: MaskedText, glossary: dict[str, str]) -> str:
    """On-screen copy: digits and units as written, STEM terms as ``term (native)``."""

    def put(match: re.Match) -> str:
        item = masked.item(match.group(0))
        if item is None:
            return match.group(0)
        if item.kind == "term":
            native = glossary.get(item.text.casefold())
            return f"{item.text} ({native})" if native else item.text
        return item.text

    return TOKEN.sub(put, translated)


def restore_voice(
    translated: str,
    masked: MaskedText,
    glossary: dict[str, str],
    language: str,
    mentioned: set[str],
) -> str:
    """Narration copy: numbers as words in ``language``; a term's native name on first mention."""

    def put(match: re.Match) -> str:
        item = masked.item(match.group(0))
        if item is None:
            return match.group(0)
        if item.kind == "term":
            key = item.text.casefold()
            native = glossary.get(key)
            if native and key not in mentioned:
                mentioned.add(key)
                return f"{item.text}, {native}"
            return item.text
        if item.kind == "equation":
            return item.text
        try:
            return spoken_quantity(item.number, item.unit, language, kind=item.kind)
        except ValueError:
            return item.text

    return TOKEN.sub(put, translated)


def tokens_in(text: str) -> list[str]:
    return TOKEN.findall(text)
