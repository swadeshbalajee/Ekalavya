"""Keep lesson reasoning canonical while localizing only learner-facing media."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from scholarmotion.schemas import SceneSpec

CANONICAL_LANGUAGE = "English"


class _LocalizedBeat(BaseModel):
    narration_segment: str
    visual: str


class _LocalizedScene(BaseModel):
    title: str
    learning_objective: str
    visual_beats: list[_LocalizedBeat] = Field(default_factory=list)
    narration: str


def _technical_glossary(language: str) -> str:
    return (
        f"Use the established {language} technical terms for every STEM concept; keep operators "
        "precise and keep symbols and equations exactly as written."
    )


def _remove_inline_math_delimiters(text: str) -> str:
    """Keep variable names readable when a presentation label uses Markdown math."""
    return re.sub(r"\$([^$]+)\$", r"\1", text)


def _is_english(language: str | None) -> bool:
    return not language or language.strip().lower() in {"en", "eng", "english"}


class TranslationService:
    """Translate at the presentation boundary, never inside planning agents.

    All persisted source scripts and scene specifications remain English. A
    localized copy is handed to the code/TTS stages so Python syntax and the
    source-grounded lesson plan stay canonical and reproducible.
    """

    def __init__(self, provider):
        self.provider = provider

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
        if not text.strip() or (_is_english(source_language) and _is_english(target_language)):
            return text
        # Offline mock mode is deliberately transparent rather than pretending
        # it has translated text. Hosted/local providers perform the real work.
        if getattr(self.provider, "name", "") == "mock":
            return text
        prompt = (
            "You are a precise educational translation service. Translate the text below "
            f"from {source_language or 'auto-detected language'} to {target_language}. "
            "Preserve every SCHOLARMOTION_STEM_LOCK_*_END token character-for-character, "
            "along with proper nouns, timestamps, and meaning. Return only the "
            "translation—no note, markdown, or quotation.\n\n"
            f"TEXT:\n{text}"
        )
        translated = (await self.provider.generate_text(prompt, temperature=0)).strip()
        if not translated:
            raise RuntimeError("translation provider returned an empty result")
        return translated

    async def localize_scene(
        self,
        spec: SceneSpec,
        *,
        text_language: str,
        voice_language: str,
    ) -> tuple[SceneSpec, str]:
        """Return localized visible text and narration from canonical English."""

        if (
            getattr(self.provider, "name", "") != "mock"
            and hasattr(self.provider, "generate_structured")
            and (not _is_english(text_language) or not _is_english(voice_language))
        ):
            canonical_fields = {
                "title": spec.title,
                "learning_objective": spec.learning_objective,
                "visual_beats": [
                    {
                        "narration_segment": beat.narration_segment,
                        "visual": beat.visual,
                    }
                    for beat in spec.visual_beats
                ],
                "narration": spec.narration,
            }
            protected: dict[str, str] = {}

            def shield(value: str) -> str:
                result = value
                for index, equation in enumerate(sorted(spec.equations, key=len, reverse=True)):
                    if equation and equation in result:
                        token = f"SCHOLARMOTION_STEM_LOCK_{index}_END"
                        protected[token] = equation
                        result = result.replace(equation, token)
                return _remove_inline_math_delimiters(result)

            canonical_fields["title"] = shield(canonical_fields["title"])
            canonical_fields["learning_objective"] = shield(canonical_fields["learning_objective"])
            canonical_fields["narration"] = shield(canonical_fields["narration"])
            canonical_fields["visual_beats"] = [
                {key: shield(value) for key, value in beat.items()}
                for beat in canonical_fields["visual_beats"]
            ]
            prompt = (
                "Translate every learner-facing string in this scene. Translate title, "
                "learning_objective, narration, and each visual-beat string to their requested "
                "language. Keep all JSON fields and beat order. Keep STEM notation, proper nouns, "
                "and every SCHOLARMOTION_STEM_LOCK_*_END token character-for-character. "
                "Narration is for speech and subtitles; visual descriptions are concise screen "
                "text and must stay concise. Do not add content.\n"
                f"ON-SCREEN TEXT GLOSSARY: {_technical_glossary(text_language)}\n"
                f"SPOKEN NARRATION GLOSSARY: {_technical_glossary(voice_language)}\n"
                f"Text language: {text_language}. Voice language: {voice_language}.\n"
                "Return the translated fields in the supplied structured schema.\n\n"
                f"SCENE_JSON:\n{json.dumps(canonical_fields, ensure_ascii=False)}"
            )
            localized = await self.provider.generate_structured(
                prompt, _LocalizedScene, temperature=0
            )

            def restore(value: str) -> str:
                result = _remove_inline_math_delimiters(value)
                for token, equation in protected.items():
                    result = result.replace(token, equation)
                return result

            if len(localized.visual_beats) != len(spec.visual_beats):
                raise RuntimeError("translation provider changed the scene beat count")
            localized_text = json.dumps(localized.model_dump(), ensure_ascii=False)
            if any(token not in localized_text for token in protected):
                raise RuntimeError(
                    "translation provider did not preserve a locked STEM notation token"
                )
            display = spec.model_copy(
                update={
                    "title": restore(localized.title),
                    "learning_objective": restore(localized.learning_objective),
                    "visual_beats": [
                        beat.model_copy(
                            update={
                                "narration_segment": restore(translated.narration_segment),
                                "visual": restore(translated.visual),
                            }
                        )
                        for beat, translated in zip(
                            spec.visual_beats, localized.visual_beats, strict=True
                        )
                    ],
                }
            )
            return display, restore(localized.narration)

        async def localize(value: str, language: str) -> str:
            # Equation strings are part of the canonical STEM payload. Shield
            # them as opaque tokens so even a fluent translator cannot mutate
            # symbols, signs, indices, or constants.
            protected: list[tuple[str, str]] = []
            translated_input = value
            for index, equation in enumerate(sorted(spec.equations, key=len, reverse=True)):
                if equation and equation in translated_input:
                    token = f"SCHOLARMOTION_STEM_LOCK_{index}_END"
                    translated_input = translated_input.replace(equation, token)
                    protected.append((token, equation))
            translated_input = _remove_inline_math_delimiters(translated_input)
            translated = await self.translate(
                translated_input, source_language="English", target_language=language
            )
            translated = _remove_inline_math_delimiters(translated)
            for token, equation in protected:
                if token not in translated:
                    raise RuntimeError(
                        "translation provider did not preserve a locked STEM notation token"
                    )
                translated = translated.replace(token, equation)
            return translated

        title = await localize(spec.title, text_language)
        objective = await localize(spec.learning_objective, text_language)
        beats = []
        for beat in spec.visual_beats:
            beats.append(
                beat.model_copy(
                    update={
                        "narration_segment": await localize(beat.narration_segment, text_language),
                        "visual": await localize(beat.visual, text_language),
                    }
                )
            )
        display_spec = spec.model_copy(
            update={"title": title, "learning_objective": objective, "visual_beats": beats}
        )
        return display_spec, await localize(spec.narration, voice_language)
