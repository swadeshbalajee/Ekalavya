"""Builds each capability's provider (with fallbacks) from settings."""

from __future__ import annotations

from scholarmotion.config import Settings
from scholarmotion.providers.bodhan import (
    BodhanTranslator,
    BodhanTTSProvider,
    LanguageRoutedTTS,
    parse_voice_map,
)
from scholarmotion.providers.embeddings.gemini import GeminiEmbeddingProvider
from scholarmotion.providers.embeddings.local import LocalEmbeddingProvider
from scholarmotion.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from scholarmotion.providers.llm.anthropic_provider import AnthropicProvider
from scholarmotion.providers.llm.gemini_provider import GeminiProvider
from scholarmotion.providers.llm.mock import MockLLMProvider
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.providers.llm.thinking import parse_rules
from scholarmotion.providers.stt.gemini import GeminiSTTProvider
from scholarmotion.providers.stt.mock import MockSTTProvider
from scholarmotion.providers.stt.openai_compatible import OpenAICompatibleSTTProvider
from scholarmotion.providers.tts.elevenlabs import ElevenLabsTTSProvider
from scholarmotion.providers.tts.gemini import GeminiTTSProvider
from scholarmotion.providers.tts.kokoro_http import KokoroHTTPTTSProvider
from scholarmotion.providers.tts.mock import MockTTSProvider
from scholarmotion.providers.tts.openai_compatible import OpenAICompatibleTTSProvider


def _provider_key(
    settings: Settings,
    provider: str,
    *specific_keys: str | None,
    base_url: str | None = None,
) -> str | None:
    provider_name = provider.lower()
    if provider_name == "gemini":
        return next((key for key in specific_keys if key), None) or settings.gemini_api_key
    if (
        provider_name in {"openai", "openai_compatible"}
        and "openrouter.ai" in (base_url or "").lower()
        and settings.openrouter_api_key
    ):
        return settings.openrouter_api_key
    return next((key for key in specific_keys if key), None)


def create_llm_provider(settings: Settings):
    fallback_models = _csv_models(settings.main_llm_fallback_models)
    return _create_llm(
        settings=settings,
        provider=settings.main_llm_provider,
        api_key=_provider_key(
            settings,
            settings.main_llm_provider,
            settings.main_llm_api_key,
            base_url=settings.main_llm_base_url,
        ),
        model=settings.main_llm_model,
        base_url=settings.main_llm_base_url,
        fallback_models=fallback_models,
        fallback_model=(
            fallback_models[0]
            if settings.main_llm_provider.lower() == "gemini" and fallback_models
            else None
        ),
        structured_reasoning_effort=settings.main_llm_structured_reasoning_effort,
    )


def create_planner_provider(settings: Settings):
    """Cheaper model for light planning steps; the main model when unset."""
    if not settings.planner_llm_model or settings.planner_llm_model == settings.main_llm_model:
        return create_llm_provider(settings)
    # Fall back to the main model so a planner outage never blocks a build.
    fallback_models = _csv_models(settings.planner_llm_fallback_models) or [settings.main_llm_model]
    return _create_llm(
        settings=settings,
        provider=settings.main_llm_provider,
        api_key=_provider_key(
            settings,
            settings.main_llm_provider,
            settings.main_llm_api_key,
            base_url=settings.main_llm_base_url,
        ),
        model=settings.planner_llm_model,
        base_url=settings.main_llm_base_url,
        fallback_models=fallback_models,
        fallback_model=(
            fallback_models[0] if settings.main_llm_provider.lower() == "gemini" else None
        ),
        structured_reasoning_effort=settings.planner_reasoning_effort,
    )


def create_guardrail_provider(settings: Settings):
    """The small model that screens learner messages; None when the screen is off."""
    if not settings.guardrail_model or settings.main_llm_provider.lower() == "mock":
        return None
    return _create_llm(
        settings=settings,
        provider=settings.main_llm_provider,
        api_key=_provider_key(
            settings,
            settings.main_llm_provider,
            settings.main_llm_api_key,
            base_url=settings.main_llm_base_url,
        ),
        model=settings.guardrail_model,
        base_url=settings.main_llm_base_url,
        # No fallback: a failed screen follows GUARDRAIL_ALLOW_ON_ERROR instead.
        fallback_models=[],
        structured_reasoning_effort="low",
    )


def _csv_models(value: str | None) -> list[str]:
    return [model.strip() for model in (value or "").split(",") if model.strip()]


def create_code_provider(settings: Settings):
    provider = settings.code_provider or settings.main_llm_provider
    model = settings.code_model or settings.main_llm_model
    configured_fallbacks = _csv_models(settings.code_fallback_models)
    return _create_llm(
        settings=settings,
        provider=provider,
        api_key=_provider_key(
            settings,
            provider,
            settings.code_api_key,
            settings.main_llm_api_key,
            base_url=settings.code_base_url or settings.main_llm_base_url,
        ),
        model=model,
        base_url=settings.code_base_url or settings.main_llm_base_url,
        fallback_models=_csv_models(settings.code_fallback_models),
        fallback_model=(
            configured_fallbacks[0]
            if provider.lower() == "gemini" and configured_fallbacks
            else (
                settings.main_llm_model
                if provider.lower() == "gemini" and model != settings.main_llm_model
                else None
            )
        ),
        code_timeout_seconds=settings.code_generation_timeout_seconds,
        reasoning_effort=settings.code_reasoning_effort,
        code_max_output_tokens=settings.code_max_output_tokens,
    )


def create_code_escalation_provider(settings: Settings):
    """The stronger code model that takes over failing scenes; None when not configured."""
    model = settings.code_escalation_model
    if not model or model == (settings.code_model or settings.main_llm_model):
        return None
    if (settings.code_provider or settings.main_llm_provider).lower() == "mock":
        return None
    return create_code_provider(
        settings.model_copy(update={"code_model": model, "code_fallback_models": ""})
    )


def create_translation_provider(settings: Settings):
    """Create the model used only for canonicalization/localization."""
    provider = settings.translation_provider or settings.main_llm_provider
    model = settings.translation_model or settings.main_llm_model
    # Translation should follow the same Gemini Pro -> Flash availability
    # policy as planning and code generation.  Without this, a project could
    # build through Flash yet fail later when it localizes its narration.
    fallback_models = _csv_models(settings.main_llm_fallback_models)
    return _create_llm(
        settings=settings,
        provider=provider,
        api_key=_provider_key(
            settings,
            provider,
            settings.translation_api_key,
            settings.main_llm_api_key,
            base_url=settings.translation_base_url or settings.main_llm_base_url,
        ),
        model=model,
        base_url=settings.translation_base_url or settings.main_llm_base_url,
        fallback_models=fallback_models,
        fallback_model=(
            fallback_models[0]
            if provider.lower() == "gemini" and fallback_models and model != fallback_models[0]
            else None
        ),
    )


def create_vision_provider(settings: Settings):
    provider = settings.visual_llm_provider or "mock"
    model = settings.visual_llm_model or "deterministic-v1"
    configured_fallbacks = _csv_models(settings.visual_llm_fallback_models)
    return _create_llm(
        settings=settings,
        provider=provider,
        api_key=_provider_key(
            settings,
            provider,
            settings.visual_llm_api_key,
            base_url=settings.visual_llm_base_url,
        ),
        model=model,
        base_url=settings.visual_llm_base_url,
        fallback_models=configured_fallbacks,
        supports_images=True,
        fallback_model=(
            configured_fallbacks[0]
            if provider.lower() == "gemini" and configured_fallbacks
            else (
                settings.main_llm_model
                if provider.lower() == "gemini" and model != settings.main_llm_model
                else None
            )
        ),
    )


def create_figure_locators(settings: Settings) -> tuple:
    """(locator, second opinion) for paper-figure highlights; None where not configured."""
    models = _csv_models(settings.figure_locator_models)
    if not models or not settings.visual_llm_provider or settings.visual_llm_provider == "mock":
        return None, None
    providers = [
        create_vision_provider(
            settings.model_copy(
                update={"visual_llm_model": model, "visual_llm_fallback_models": ""}
            )
        )
        for model in models[:2]
    ]
    return providers[0], (providers[1] if len(providers) > 1 else None)


def create_video_provider(settings: Settings):
    """Build the configured rendered-scene reviewer, including frame-only Gemini QA."""
    if not settings.video_verification_enabled:
        return None
    provider = settings.video_llm_provider
    if provider.lower() == "gemini":
        api_key = _provider_key(settings, provider, settings.video_llm_api_key)
        if not api_key:
            return None
        configured_fallbacks = _csv_models(settings.video_llm_fallback_models)
        return GeminiProvider(
            api_key=api_key,
            model=settings.video_llm_model,
            default_temperature=settings.agent_temperature,
            fallback_model=configured_fallbacks[0] if configured_fallbacks else None,
        )
    base_url = settings.video_llm_base_url
    api_key = _provider_key(
        settings,
        provider,
        settings.video_llm_api_key,
        base_url=base_url,
    )
    if not api_key:
        return None
    return _create_llm(
        settings=settings,
        provider=provider,
        api_key=api_key,
        model=settings.video_llm_model,
        base_url=base_url,
        fallback_models=_csv_models(settings.video_llm_fallback_models),
        supports_images=True,
        code_timeout_seconds=90,
        reasoning_effort=settings.video_llm_reasoning_effort,
    )


def _create_llm(
    *,
    settings: Settings,
    provider: str,
    api_key: str | None,
    model: str,
    base_url: str | None,
    supports_images: bool = False,
    fallback_model: str | None = None,
    fallback_models: list[str] | None = None,
    code_timeout_seconds: int | None = None,
    reasoning_effort: str = "low",
    code_max_output_tokens: int = 6000,
    structured_reasoning_effort: str = "high",
):
    name = provider.lower()
    if name == "mock":
        return MockLLMProvider(model)
    if not api_key:
        raise ValueError(f"API key is required for {name}")
    if name == "anthropic":
        return AnthropicProvider(
            api_key=api_key, model=model, default_temperature=settings.agent_temperature
        )
    if name == "gemini":
        return GeminiProvider(
            api_key=api_key,
            model=model,
            fallback_model=fallback_model,
            default_temperature=settings.agent_temperature,
        )
    if name in {"openai", "openai_compatible"}:
        return OpenAICompatibleProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            supports_images=supports_images or name == "openai",
            fallback_models=fallback_models,
            code_timeout_seconds=code_timeout_seconds or 25,
            code_max_output_tokens=code_max_output_tokens,
            reasoning_effort=reasoning_effort,
            structured_reasoning_effort=structured_reasoning_effort,
            default_temperature=settings.agent_temperature,
            request_timeout_seconds=settings.llm_request_timeout_seconds,
            pdf_engine=settings.openrouter_pdf_engine,
            thinking_rules=parse_rules(settings.model_thinking),
        )
    raise ValueError(f"unknown LLM provider: {name}")


def create_machine_translator(settings: Settings) -> BodhanTranslator | None:
    """Bodhan translation when a key is configured, else None (LLM translation only)."""
    api_key = settings.bodhan_translate_api_key or settings.bodhan_api_key
    if not api_key:
        return None
    return BodhanTranslator(
        api_key=api_key, base_url=settings.bodhan_base_url, usd_per_inr=settings.usd_per_inr
    )


def create_tts_provider(settings: Settings):
    """The narration provider, routed to Bodhan for its languages when a key is set."""
    provider = _create_base_tts_provider(settings)
    api_key = settings.bodhan_tts_api_key or settings.bodhan_api_key
    if not api_key:
        return provider
    bodhan = BodhanTTSProvider(
        api_key=api_key,
        voice=settings.bodhan_tts_voice,
        voices=parse_voice_map(settings.bodhan_tts_voices),
        style=settings.bodhan_tts_style,
        base_url=settings.bodhan_base_url,
        usd_per_inr=settings.usd_per_inr,
    )
    return LanguageRoutedTTS(bodhan, provider)


def _create_base_tts_provider(settings: Settings):
    if settings.tts_provider.lower() == "mock":
        return MockTTSProvider(settings.tts_model)
    if settings.tts_provider.lower() == "gemini":
        api_key = _provider_key(settings, "gemini", settings.tts_api_key)
        if not api_key:
            raise ValueError("GEMINI_API_KEY or TTS_API_KEY is required for Gemini TTS")
        return GeminiTTSProvider(
            api_key=api_key,
            model=settings.tts_model,
            voice=settings.tts_voice,
        )
    if settings.tts_provider.lower() == "elevenlabs":
        if not settings.tts_api_key:
            raise ValueError("TTS_API_KEY is required for elevenlabs")
        return ElevenLabsTTSProvider(
            api_key=settings.tts_api_key, model=settings.tts_model, voice_id=settings.tts_voice
        )
    if settings.tts_provider.lower() == "kokoro_http":
        if not settings.tts_base_url:
            raise ValueError("TTS_BASE_URL is required for kokoro_http")
        return KokoroHTTPTTSProvider(base_url=settings.tts_base_url, voice=settings.tts_voice)
    if settings.tts_provider.lower() in {"openai", "openai_compatible"}:
        if not settings.tts_voice or (
            settings.tts_model.startswith("fish-audio/") and settings.tts_voice == "alloy"
        ):
            raise ValueError(
                "Configure a provider-supported TTS_VOICE to keep the same narrator across scenes"
            )
        api_key = _provider_key(
            settings,
            settings.tts_provider,
            settings.tts_api_key,
            base_url=settings.tts_base_url,
        )
        if not api_key:
            raise ValueError("TTS_API_KEY is required")
        return OpenAICompatibleTTSProvider(
            api_key=api_key,
            model=settings.tts_model,
            base_url=settings.tts_base_url or "https://api.openai.com/v1",
            voice=settings.tts_voice,
            ffmpeg_binary=settings.ffmpeg_binary,
            instructions=settings.tts_instructions,
            response_format=settings.tts_response_format,
            pcm_sample_rate=settings.tts_pcm_sample_rate,
        )
    raise RuntimeError(
        f"TTS provider {settings.tts_provider!r} is not bundled; register an adapter implementing TTSProvider"
    )


def create_stt_provider(settings: Settings):
    if settings.stt_provider.lower() == "mock":
        return MockSTTProvider(settings.stt_model)
    if settings.stt_provider.lower() == "gemini":
        api_key = _provider_key(settings, "gemini", settings.stt_api_key)
        if not api_key:
            raise ValueError("GEMINI_API_KEY or STT_API_KEY is required for Gemini STT")
        return GeminiSTTProvider(
            api_key=api_key,
            model=settings.stt_model,
        )
    if settings.stt_provider.lower() in {"openai", "openai_compatible"}:
        api_key = _provider_key(
            settings,
            settings.stt_provider,
            settings.stt_api_key,
            base_url=settings.stt_base_url,
        )
        if not api_key:
            raise ValueError("STT_API_KEY is required")
        return OpenAICompatibleSTTProvider(
            api_key=api_key,
            model=settings.stt_model,
            base_url=settings.stt_base_url or "https://api.openai.com/v1",
        )
    raise RuntimeError(
        f"STT provider {settings.stt_provider!r} is not bundled; register an adapter implementing STTProvider"
    )


def create_embedding_provider(settings: Settings):
    if settings.embedding_provider.lower() == "gemini":
        api_key = _provider_key(settings, "gemini", settings.embedding_api_key)
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY or EMBEDDING_API_KEY is required for Gemini embeddings"
            )
        return GeminiEmbeddingProvider(
            api_key=api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    if settings.embedding_provider.lower() in {"openai", "openai_compatible"}:
        api_key = _provider_key(
            settings,
            settings.embedding_provider,
            settings.embedding_api_key,
            base_url=settings.embedding_base_url,
        )
        if not api_key:
            raise ValueError("EMBEDDING_API_KEY is required")
        return OpenAICompatibleEmbeddingProvider(
            api_key=api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url or "https://api.openai.com/v1",
            dimensions=settings.embedding_dimensions,
        )
    return LocalEmbeddingProvider(settings.embedding_model)
