"""Typed settings for providers, storage, services, and sandbox limits."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env.local is intentionally ignored by Git. It lets a local deployment
    # override checked-in defaults without placing credentials in source control.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), extra="ignore", case_sensitive=False
    )

    environment: str = Field("development", validation_alias="SCHOLARMOTION_ENV")
    app_api_key: str | None = None
    admin_api_key: str | None = None
    media_cors_origins: str = "http://127.0.0.1:8501,http://localhost:8501"
    database_url: str = (
        "postgresql+asyncpg://scholarmotion:scholarmotion@127.0.0.1:5432/scholarmotion"
    )
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "scholarmotion"
    object_storage_backend: str = "local"
    object_storage_root: Path = Path("projects")
    s3_endpoint_url: str | None = None
    s3_bucket: str = "scholarmotion"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    main_llm_provider: str = "mock"
    # Shared Gemini credential used when a capability-specific API key is not
    # set. Keeping one secret avoids duplicating the same key across the .env.
    gemini_api_key: str | None = None
    # One OpenRouter key can serve every capability configured with an
    # OpenAI-compatible adapter and an OpenRouter base URL.
    openrouter_api_key: str | None = None
    main_llm_api_key: str | None = None
    main_llm_model: str = "deterministic-v1"
    main_llm_fallback_models: str = ""
    main_llm_base_url: str | None = None
    main_llm_temperature: float = 0.2
    # Sampling temperature for every agent call (planner, script, storyboard,
    # code, translation, reviews) unless a call sets its own.
    agent_temperature: float = Field(default=0.7, ge=0, le=2)
    # Shortest time each model call may take before the next fallback model is tried.
    # Free models can need several minutes; calls that already allow longer keep that.
    llm_request_timeout_seconds: int = Field(default=90, ge=10, le=1800)
    # Stages that write a whole lesson in one answer (script, storyboard, lesson
    # review) take minutes for long lectures; each of their requests gets this.
    llm_long_request_timeout_seconds: int = Field(default=420, ge=10, le=1800)
    # Thinking budget for the main model's structured outputs (script drafts
    # and reviews). Reasoning tokens bill as output tokens.
    main_llm_structured_reasoning_effort: str = Field(default="high", pattern="^(low|medium|high)$")
    # How OpenRouter turns a paper PDF into text for models that cannot read
    # files themselves; models that can always get the PDF as is.
    openrouter_pdf_engine: str = Field(
        default="cloudflare-ai", pattern="^(cloudflare-ai|mistral-ocr)$"
    )
    # A cheaper model for light planning steps: intent/language preparation,
    # storyboard beats and the final lesson-flow review. Unset uses the main
    # model. It shares the main model's provider, key and base URL.
    planner_llm_model: str | None = None
    planner_llm_fallback_models: str = ""
    planner_reasoning_effort: str = Field(default="low", pattern="^(low|medium|high)$")
    # Measure when each narration word is spoken with a local CTC aligner, so
    # scene cuts and animation cues follow the recording instead of estimates.
    narration_alignment: bool = True
    narration_aligner_model: str = "MahmoudAshraf/mms-300m-1130-forced-aligner"
    # Translation is independent so a low-cost multilingual model can localize
    # presentation text without changing the canonical English lesson agents.
    translation_provider: str | None = None
    translation_api_key: str | None = None
    translation_model: str | None = None
    translation_base_url: str | None = None
    # Each capability can run against its own model API. The code generator
    # must not silently inherit a general-purpose planning deployment.
    code_provider: str | None = None
    code_api_key: str | None = None
    code_model: str | None = None
    code_fallback_models: str = ""
    code_base_url: str | None = None
    code_generation_timeout_seconds: int = 60
    code_reasoning_effort: str = Field(default="low", pattern="^(low|medium|high)$")
    code_max_output_tokens: int = Field(default=6000, ge=1024, le=64000)
    # A stronger model that takes over a scene after the code model has failed
    # it this many times: it first fixes the failing code, then starts fresh.
    # Empty disables the hand-over.
    code_escalation_model: str | None = None
    code_escalation_after_failures: int = Field(default=2, ge=1, le=5)
    # Thinking rule per model, overriding the effort levels above:
    # "model=6000" caps thinking tokens, "model=off" turns thinking off,
    # "model=low|medium|high" sets an effort. A key ending in "/" matches every
    # model of that provider. Some open models ignore effort levels and spend
    # the whole output budget thinking unless capped.
    # Measured on real scene prompts (Oct 2026): GLM-5.3 Flash plans in ~6k
    # tokens and needs the cap only as a guard; Qwen 3.8 Omni Flash took 127 s
    # to review a 30 s clip while thinking and found less than with it off.
    model_thinking: str = (
        "z-ai/glm-5.3-flash=6000,z-ai/glm-5.3=8000,qwen/qwen3.8-omni-flash=off,"
        "qwen/=4000,deepseek/=4000,moonshotai/=4000"
    )
    visual_llm_provider: str | None = None
    visual_llm_api_key: str | None = None
    visual_llm_model: str | None = None
    visual_llm_fallback_models: str = ""
    visual_llm_base_url: str | None = None
    video_verification_enabled: bool = True
    video_llm_provider: str = "openai_compatible"
    video_llm_api_key: str | None = None
    video_llm_model: str = "google/gemini-2.5-flash-lite"
    video_llm_fallback_models: str = "google/gemini-2.5-flash"
    video_llm_base_url: str = "https://openrouter.ai/api/v1"
    video_llm_reasoning_effort: str = "low"
    # False: the video reviewer alone decides whether a scene passes; geometry
    # measurements are repair hints. True restores the strict geometry gate.
    static_layout_gate: bool = False
    # Default thinking budget for tutor explanations when the learner does not
    # choose one; applied only by providers that support reasoning effort.
    tutor_reasoning_effort: str = Field(default="medium", pattern="^(low|medium|high)$")
    # Search the web when neither attached papers nor the vector library have
    # relevant material (OpenRouter web plugin). Citations are shown to learners.
    web_search_enabled: bool = True
    web_search_max_results: int = Field(default=5, ge=1, le=10)
    tts_provider: str = "mock"
    tts_api_key: str | None = None
    tts_model: str = "deterministic-v1"
    tts_base_url: str | None = None
    tts_voice: str = "alloy"
    tts_instructions: str = (
        "Speak as a warm, clear STEM tutor. Use a conversational, unhurried cadence, "
        "natural pauses and the same vocal identity throughout. Read only the supplied narration, "
        "in its language. Pronounce mathematical letter names and units clearly. "
        "Do not improvise, sing, or read punctuation as words. Let the final sentence settle naturally."
    )
    tts_response_format: str = "mp3"
    tts_pcm_sample_rate: int = Field(default=24000, ge=8000, le=96000)
    # Bodhan AI (https://console.bodhan.ai). When a key is set, translation and
    # narration for the languages Bodhan covers go there; everything else, and
    # any failed Bodhan call, uses the providers configured above.
    bodhan_api_key: str | None = None
    bodhan_translate_api_key: str | None = None
    bodhan_tts_api_key: str | None = None
    bodhan_base_url: str = "https://api.bodhan.ai"
    bodhan_tts_voice: str = "Kavya"
    # Per-language voices, e.g. "ta:Name,te:Name"; unlisted languages use bodhan_tts_voice.
    bodhan_tts_voices: str = ""
    bodhan_tts_style: str = "educational lecture"
    # Dollars per rupee, for showing Bodhan's INR prices in dollars.
    usd_per_inr: float = Field(default=0.012, gt=0)
    # Usernames (comma-separated) whose usage dashboard can switch to all users.
    admin_usernames: str = "admin"
    stt_provider: str = "mock"
    stt_api_key: str | None = None
    stt_model: str = "deterministic-v1"
    stt_base_url: str | None = None
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_source_collection: str = "scholarmotion_source_chunks"
    qdrant_skill_collection: str = "scholarmotion_skills"
    qdrant_knowledge_collection: str = "scholarmotion_knowledge"

    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None

    max_concurrent_scenes: int = 4
    max_render_workers: int = 2
    max_llm_requests: int = 4
    render_timeout_seconds: int = 180
    ffmpeg_binary: str = "ffmpeg"
    manim_binary: str = "manim"
    sandbox_memory_limit_mb: int = 2048
    sandbox_cpu_seconds: int = 180
    # auto uses bubblewrap when present; required refuses to render without an
    # OS sandbox and is the production setting.
    sandbox_isolation: str = "auto"
    # RLIMIT_NPROC is per Unix user, not per render. Leave it disabled for a
    # developer's shared login; enable it only for a dedicated service user or
    # an already isolated container/cgroup.
    sandbox_process_limit: int = 0
    # Optional production tracing exporter. Metrics are always available at
    # /metrics for a Prometheus-compatible scraper.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "scholarmotion"

    @property
    def is_mock(self) -> bool:
        return self.main_llm_provider.lower() == "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
