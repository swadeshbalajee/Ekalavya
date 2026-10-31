from scholarmotion.config import Settings
from scholarmotion.providers.factory import (
    create_code_provider,
    create_llm_provider,
    create_stt_provider,
    create_translation_provider,
    create_tts_provider,
    create_video_provider,
    create_vision_provider,
)


def test_openrouter_key_is_used_for_all_capabilities_with_openrouter_base_url():
    settings = Settings(
        openrouter_api_key="shared-openrouter-key",
        main_llm_provider="openai_compatible",
        main_llm_api_key="stale-main-key",
        main_llm_model="planner",
        main_llm_base_url="https://openrouter.ai/api/v1",
        code_provider="openai_compatible",
        code_api_key="stale-code-key",
        code_model="coder",
        code_base_url="https://openrouter.ai/api/v1",
        visual_llm_provider="openai_compatible",
        visual_llm_api_key="stale-vision-key",
        visual_llm_model="vision",
        visual_llm_base_url="https://openrouter.ai/api/v1",
        video_llm_provider="openai_compatible",
        video_verification_enabled=True,
        video_llm_fallback_models="google/gemini-2.5-flash",
        tts_provider="openai_compatible",
        tts_api_key="stale-tts-key",
        tts_model="fish-audio/test",
        tts_voice="configured-narrator",
        tts_base_url="https://openrouter.ai/api/v1",
        stt_provider="openai_compatible",
        stt_api_key="stale-stt-key",
        stt_model="openai/whisper-1",
        stt_base_url="https://openrouter.ai/api/v1",
        # A Bodhan key in the developer's .env routes narration through Bodhan.
        bodhan_api_key=None,
        bodhan_tts_api_key=None,
        bodhan_translate_api_key=None,
    )

    providers = [
        create_llm_provider(settings),
        create_code_provider(settings),
        create_vision_provider(settings),
        create_video_provider(settings),
        create_tts_provider(settings),
        create_stt_provider(settings),
    ]

    assert [provider.client.api_key for provider in providers[:4]] == ["shared-openrouter-key"] * 4
    assert providers[3].fallback_models == ["google/gemini-2.5-flash"]
    assert [provider.api_key for provider in providers[4:]] == ["shared-openrouter-key"] * 2


def test_video_provider_can_use_configured_direct_gemini_frame_reviewer():
    from scholarmotion.providers.llm.gemini_provider import GeminiProvider

    settings = Settings(
        gemini_api_key="direct-gemini-key",
        video_llm_provider="gemini",
        video_verification_enabled=True,
        video_llm_model="gemini-3.8-flash",
        main_llm_provider="gemini",
        main_llm_model="gemini-3.8-flash",
    )

    provider = create_video_provider(settings)

    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-3.8-flash"


def test_gemini_code_and_video_fallbacks_use_capability_specific_models():
    from scholarmotion.providers.llm.gemini_provider import GeminiProvider

    settings = Settings(
        gemini_api_key="direct-gemini-key",
        main_llm_provider="gemini",
        main_llm_model="gemini-main",
        code_provider="gemini",
        code_model="gemini-code",
        code_fallback_models="gemini-code-backup",
        video_llm_provider="gemini",
        video_verification_enabled=True,
        video_llm_model="gemini-video",
        video_llm_fallback_models="gemini-video-backup",
    )

    code = create_code_provider(settings)
    video = create_video_provider(settings)

    assert isinstance(code, GeminiProvider)
    assert code.fallback_model == "gemini-code-backup"
    assert isinstance(video, GeminiProvider)
    assert video.fallback_model == "gemini-video-backup"


def test_gemini_visual_provider_uses_its_configured_fallback():
    from scholarmotion.providers.llm.gemini_provider import GeminiProvider

    settings = Settings(
        gemini_api_key="direct-gemini-key",
        main_llm_provider="gemini",
        main_llm_model="gemini-main",
        visual_llm_provider="gemini",
        visual_llm_model="gemini-vision",
        visual_llm_fallback_models="gemini-vision-backup",
    )

    visual = create_vision_provider(settings)

    assert isinstance(visual, GeminiProvider)
    assert visual.fallback_model == "gemini-vision-backup"


def test_gemini_tutor_uses_first_configured_main_model_fallback():
    from scholarmotion.providers.llm.gemini_provider import GeminiProvider

    settings = Settings(
        gemini_api_key="direct-gemini-key",
        main_llm_provider="gemini",
        main_llm_model="gemini-main",
        main_llm_fallback_models="gemini-backup,gemini-second-backup",
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-main"
    assert provider.fallback_model == "gemini-backup"


def test_gemini_translation_uses_flash_fallback_when_its_primary_is_unavailable():
    from scholarmotion.providers.llm.gemini_provider import GeminiProvider

    settings = Settings(
        gemini_api_key="direct-gemini-key",
        main_llm_provider="gemini",
        main_llm_model="gemini-pro",
        main_llm_fallback_models="gemini-flash",
        translation_provider="gemini",
        translation_model="gemini-pro",
    )

    provider = create_translation_provider(settings)

    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-pro"
    assert provider.fallback_model == "gemini-flash"
