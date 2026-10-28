"""Keep lesson reasoning canonical while localizing only learner-facing media.

Translation is checked, not trusted. Before translating, numbers, units,
equations and the lesson's key STEM terms are replaced by placeholders
(:mod:`.entities`); afterwards each result is checked against its English
source (:mod:`.checks`). A failed string gets one corrected retry from the LLM
translator; if that also fails, screen text stays in English and the problem
is recorded in :attr:`TranslationService.events`. Bodhan translates the
languages it covers when configured; the LLM translator handles the rest.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from pydantic import BaseModel, Field

from scholarmotion.schemas import SceneSpec
from scholarmotion.translation.checks import translation_problems, unspeakable
from scholarmotion.translation.entities import (
    TOKEN,
    MaskedText,
    mask,
    restore_screen,
    restore_voice,
)

logger = logging.getLogger(__name__)

CANONICAL_LANGUAGE = "English"
MAX_PARALLEL_TRANSLATIONS = 4


class _Batch(BaseModel):
    translations: list[str] = Field(default_factory=list)


class _GlossaryEntry(BaseModel):
    english: str
    native: str


class _Glossary(BaseModel):
    terms: list[_GlossaryEntry] = Field(default_factory=list)


def _remove_inline_math_delimiters(text: str) -> str:
    """Keep variable names readable when a presentation label uses Markdown math."""
    return re.sub(r"\$([^$]+)\$", r"\1", text)


def _is_english(language: str | None) -> bool:
    return not language or language.strip().lower() in {"en", "eng", "english"}


def _rules(language: str) -> str:
    return (
        f"Translate from English to {language} for a STEM lesson. Placeholders such as "
        "[[ZQA]] stand for numbers, units, formulas and technical terms: copy every "
        "placeholder exactly once, unchanged, where it belongs in the sentence. Write no "
        "digits of your own. Keep each person, place and organisation name, written in "
        f"{language} script or as given. Drop English articles (the, a) in front of a "
        "placeholder when the target language has none. Do not add or drop content."
    )


class TranslationService:
    """Translate at the presentation boundary, never inside planning agents.

    All persisted source scripts and scene specifications remain English. A
    localized copy is handed to the code/TTS stages so Python syntax and the
    source-grounded lesson plan stay canonical and reproducible.
    """

    def __init__(self, provider, machine=None):
        self.provider = provider
        # Optional machine translator (Bodhan) used first for the languages it covers.
        self.machine = machine
        self.events: list[dict] = []
        self._glossary: dict[tuple[str, str], str] = {}
        self._limit = asyncio.Semaphore(MAX_PARALLEL_TRANSLATIONS)

    @property
    def _offline(self) -> bool:
        return getattr(self.provider, "name", "") == "mock" and self.machine is None

    def _machine_for(self, language: str):
        machine = self.machine
        return machine if machine is not None and machine.supports(language) else None

    async def to_english(self, text: str, *, source_language: str | None = None) -> str:
        return await self.translate(
            text, source_language=source_language or "auto", target_language=CANONICAL_LANGUAGE
        )

    async def translate(
        self,
        text: str,
        *,
        source_language: str | None = None,
        target_language: str = CANONICAL_LANGUAGE,
    ) -> str:
        """Checked translation of free text (chat replies, requests)."""
        if not text.strip() or (_is_english(source_language) and _is_english(target_language)):
            return text
        # Offline mock mode is deliberately transparent rather than pretending
        # it has translated text. Hosted/local providers perform the real work.
        if self._offline:
            return text
        masked = mask(text)
        [result] = await self._translate_all([masked], target_language)
        if result is None:
            if _is_english(target_language):
                raise RuntimeError("translation to English failed its checks")
            return text
        return restore_screen(result, masked, {})

    async def localize_scene(
        self,
        spec: SceneSpec,
        *,
        text_language: str,
        voice_language: str,
    ) -> tuple[SceneSpec, str]:
        """Return localized visible text and narration from canonical English."""
        if self._offline or (_is_english(text_language) and _is_english(voice_language)):
            return spec, spec.narration
        terms = tuple(spec.key_terms)
        equations = tuple(spec.equations)

        def protect(value: str) -> MaskedText:
            return mask(value, key_terms=terms, equations=equations)

        screen_sources = [
            spec.title,
            spec.learning_objective,
            *(
                value
                for beat in spec.visual_beats
                for value in (beat.narration_segment, beat.visual)
            ),
        ]
        screen = list(screen_sources)
        if not _is_english(text_language):
            glossary = await self.glossary(terms, text_language)
            masked = [protect(value) for value in screen_sources]
            results = await self._translate_all(masked, text_language)
            screen = [
                _remove_inline_math_delimiters(
                    restore_screen(result, item, glossary) if result is not None else item.source
                )
                for item, result in zip(masked, results, strict=True)
            ]
        narration = spec.narration
        if not _is_english(voice_language):
            voice_glossary = await self.glossary(terms, voice_language)
            masked_narration = protect(spec.narration)
            [result] = await self._translate_all([masked_narration], voice_language)
            if result is not None:
                narration = restore_voice(
                    result, masked_narration, voice_glossary, voice_language, set()
                )
            leftover = unspeakable(narration)
            if leftover:
                self._event(
                    "speech_symbols", spec.narration, [f"left in narration: {' '.join(leftover)}"]
                )
        beats = [
            beat.model_copy(update={"narration_segment": segment, "visual": visual})
            for beat, segment, visual in zip(
                spec.visual_beats, screen[2::2], screen[3::2], strict=True
            )
        ]
        display = spec.model_copy(
            update={"title": screen[0], "learning_objective": screen[1], "visual_beats": beats}
        )
        return display, narration

    async def glossary(self, terms: tuple[str, ...] | list[str], language: str) -> dict[str, str]:
        """Native names for the lesson's key terms in ``language`` (cached per lesson)."""
        if _is_english(language) or not terms:
            return {}
        missing = [term for term in terms if (term.casefold(), language) not in self._glossary]
        machine = self._machine_for(language)
        if missing and machine is not None:

            async def native(term: str) -> tuple[str, str | None]:
                try:
                    async with self._limit:
                        result = await machine.translate(term, language)
                    return term, result.text if not result.truncated else None
                except Exception as error:  # noqa: BLE001 - the LLM fills the gap below
                    logger.warning("Glossary term %r failed on %s: %s", term, machine.name, error)
                    return term, None

            for term, value in await asyncio.gather(*(native(term) for term in missing)):
                if _usable_term(term, value):
                    self._glossary[(term.casefold(), language)] = value.strip()
            missing = [term for term in terms if (term.casefold(), language) not in self._glossary]
        if missing and hasattr(self.provider, "generate_structured") and not self._offline:
            prompt = (
                f"Give the established {language} name for each STEM term, as used in "
                f"{language}-medium textbooks. Return one entry per term, with `english` "
                "exactly as given.\n" + "\n".join(f"- {term}" for term in missing)
            )
            try:
                result = await self.provider.generate_structured(prompt, _Glossary, temperature=0)
                for entry in result.terms:
                    if entry.english in missing and _usable_term(entry.english, entry.native):
                        self._glossary[(entry.english.casefold(), language)] = entry.native.strip()
            except Exception as error:  # noqa: BLE001 - terms then show in English only
                logger.warning("Glossary for %s failed: %s", language, error)
        return {
            term.casefold(): self._glossary[(term.casefold(), language)]
            for term in terms
            if (term.casefold(), language) in self._glossary
        }

    async def _translate_all(self, items: list[MaskedText], language: str) -> list[str | None]:
        """Translate and check each item; None where it still fails after one fix."""
        drafts: list[str | None] = [None] * len(items)
        truncated = [False] * len(items)
        # Strings that are only placeholders, punctuation or blank need no translator.
        pending = [index for index, item in enumerate(items) if _needs_translation(item)]
        for index in set(range(len(items))) - set(pending):
            drafts[index] = items[index].text
        machine = self._machine_for(language)
        if pending and machine is not None:

            async def run(index: int) -> None:
                try:
                    async with self._limit:
                        result = await machine.translate(items[index].text, language)
                    drafts[index], truncated[index] = result.text, result.truncated
                except Exception as error:  # noqa: BLE001 - the LLM translator retries it
                    logger.warning("%s translation failed: %s", machine.name, error)

            await asyncio.gather(*(run(index) for index in range(len(items))))
        elif len(items) > 1 and hasattr(self.provider, "generate_structured"):
            prompt = (
                _rules(language)
                + f"\nTranslate each string in the JSON list; return exactly {len(items)} "
                "translations in the same order.\n"
                + json.dumps([item.text for item in items], ensure_ascii=False)
            )
            try:
                batch = await self.provider.generate_structured(prompt, _Batch, temperature=0)
                if len(batch.translations) == len(items):
                    drafts = list(batch.translations)
            except Exception as error:  # noqa: BLE001 - each string is retried alone below
                logger.warning("Batch translation failed: %s", error)

        async def settle(index: int) -> str | None:
            item = items[index]
            draft = drafts[index]
            if draft is None:
                # No machine or batch result: the LLM makes the first attempt.
                draft = await self._llm_translate(item, language, [])
            problems = (
                translation_problems(
                    item, draft, target_language=language, truncated=truncated[index]
                )
                if draft is not None
                else ["no translation was returned"]
            )
            if not problems:
                return draft
            fixed = await self._llm_translate(item, language, problems)
            remaining = (
                translation_problems(item, fixed, target_language=language)
                if fixed is not None
                else ["no translation was returned"]
            )
            if not remaining:
                self._event("fixed", item.source, problems)
                return fixed
            self._event("fallback", item.source, remaining)
            return None

        return list(await asyncio.gather(*(settle(index) for index in range(len(items)))))

    async def _llm_translate(
        self, item: MaskedText, language: str, problems: list[str]
    ) -> str | None:
        """One LLM translation of a masked string, told what the last attempt got wrong."""
        if getattr(self.provider, "name", "") == "mock" or not hasattr(
            self.provider, "generate_text"
        ):
            return None
        feedback = (
            "\nA previous translation was rejected: "
            + "; ".join(problems[:6])
            + ". Fix these problems."
            if problems and problems != ["no translation was returned"]
            else ""
        )
        prompt = (
            f"{_rules(language)}{feedback}\nReturn only the translation, with no notes.\n\n"
            f"TEXT:\n{item.text}"
        )
        try:
            async with self._limit:
                return (await self.provider.generate_text(prompt, temperature=0)).strip()
        except Exception as error:  # noqa: BLE001 - reported as a fallback by the caller
            logger.warning("LLM translation failed: %s", error)
            return None

    def _event(self, status: str, source: str, problems: list[str]) -> None:
        self.events.append({"status": status, "text": source[:120], "problems": problems[:6]})


def _needs_translation(item: MaskedText) -> bool:
    return any(ch.isalpha() for ch in TOKEN.sub("", item.text))


def _usable_term(term: str, native: str | None) -> bool:
    """A glossary entry worth showing: non-empty, translated, short, no digits."""
    if not native or not native.strip():
        return False
    value = native.strip()
    return (
        value.casefold() != term.casefold()
        and len(value) <= 80
        and not any(ch.isdigit() for ch in value)
    )
