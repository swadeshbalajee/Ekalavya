"""Check a translation against the entities found in its English source.

No model is run on the translated text. Every placeholder must come back
exactly once, the translator must not introduce digits of its own (all
numbers are placeholders), and the output must be neither truncated nor left
untranslated. Names are checked too (verbatim, or transliterated and matched
after romanizing with ``uroman``) but only noted. Speech text must contain no
digits or symbols.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from functools import cache

from scholarmotion.translation.entities import TOKEN, MaskedText

logger = logging.getLogger(__name__)

NAME_MATCH_RATIO = 0.6
_UNSPEAKABLE = re.compile(r"[%°^=³²¹⁰⁴⁵⁶⁷⁸⁹+<>/\\]")


@cache
def _romanizer():
    try:
        import uroman

        return uroman.Uroman()
    except Exception as error:  # noqa: BLE001 - optional dependency
        logger.info("uroman unavailable; transliterated names are not checked: %s", error)
        return None


def _letters(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalpha())


def name_present(name: str, translated: str) -> bool:
    """The name is written as-is, or a transliteration romanizes close to it."""
    if name.casefold() in translated.casefold():
        return True
    roman = _romanizer()
    if roman is None:
        return True
    target = _letters(name)
    if not target:
        return True
    words = [_letters(roman.romanize_string(word)) for word in translated.split()]
    width = max(1, len(name.split()))
    windows = ("".join(words[i : i + width]) for i in range(max(1, len(words) - width + 1)))

    def covered(window: str) -> float:
        # Share of the name's letters found in order: tolerant of case endings
        # added in agglutinative languages (Sodium -> sodiyattin).
        blocks = difflib.SequenceMatcher(None, target, window).get_matching_blocks()
        return sum(block.size for block in blocks) / len(target)

    return any(covered(window) >= NAME_MATCH_RATIO for window in windows if window)


def translation_problems(
    masked: MaskedText,
    translated: str,
    *,
    target_language: str,
    truncated: bool = False,
) -> list[str]:
    """What the translation lost or changed; empty when it can be used."""
    problems: list[str] = []
    if not translated.strip():
        return ["the translation is empty"]
    if truncated:
        problems.append("the translation was cut off")
    for item in masked.items:
        count = translated.count(item.token)
        if count != 1:
            state = "is missing" if count == 0 else f"appears {count} times"
            problems.append(f"{item.token} ({item.kind} {item.text!r}) {state}")
    unexpected = set(TOKEN.findall(translated)) - {item.token for item in masked.items}
    if unexpected:
        problems.append(f"unknown placeholders {sorted(unexpected)}")
    added = sorted({ch for ch in TOKEN.sub("", translated) if unicodedata.category(ch) == "Nd"})
    if added:
        problems.append(
            f"digits {' '.join(added)} were added (every number must stay a placeholder)"
        )
    source_words = re.findall(r"[A-Za-z]{3,}", TOKEN.sub("", masked.text))
    if (
        target_language.strip().lower() not in {"english", "en"}
        and len(source_words) >= 4
        and TOKEN.sub("", translated).split() == TOKEN.sub("", masked.text).split()
    ):
        problems.append("the text was not translated")
    source_length = len(TOKEN.sub("", masked.text))
    if source_length >= 40 and len(TOKEN.sub("", translated)) < source_length * 0.3:
        problems.append("the translation is much shorter than the source")
    return problems


def missing_names(masked: MaskedText, translated: str) -> list[str]:
    """Names from the source not found in the translation.

    Reported as notes, not failures: NER also tags capitalised common words,
    so a missing "name" is not proof the translation is wrong.
    """
    return [name for name in masked.names if not name_present(name, translated)]


def unspeakable(text: str) -> list[str]:
    """Digits and symbols left in a narration copy, which TTS would misread."""
    found = sorted({ch for ch in text if unicodedata.category(ch) == "Nd"})
    found += sorted(set(_UNSPEAKABLE.findall(text)))
    return found
