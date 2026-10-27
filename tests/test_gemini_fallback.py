from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import scholarmotion.providers.llm.gemini_provider as gemini_module
from scholarmotion.providers.llm.gemini_provider import GeminiProvider


@pytest.mark.asyncio
async def test_gemini_switches_to_configured_fallback_on_quota_error():
    calls: list[str] = []

    async def generate_content(*, model, contents, config):
        calls.append(model)
        if model == "gemini-3.7-flash":
            raise RuntimeError("429 RESOURCE_EXHAUSTED free-tier quota exceeded")
        return SimpleNamespace(text="class Lesson(Scene): pass")

    provider = object.__new__(GeminiProvider)
    provider.model = "gemini-3.7-flash"
    provider.fallback_model = "gemini-3.5-flash-lite"
    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    result = await provider._generate("write a Manim scene", {})

    assert result.text == "class Lesson(Scene): pass"
    assert calls == ["gemini-3.7-flash", "gemini-3.5-flash-lite"]


@pytest.mark.asyncio
async def test_daily_gemini_quota_fails_without_minute_long_retries():
    calls = 0

    async def generate_content(*, model, contents, config):
        nonlocal calls
        calls += 1
        raise RuntimeError(
            "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        )

    provider = object.__new__(GeminiProvider)
    provider.model = "gemini-3.5-flash-lite"
    provider.fallback_model = None
    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        await provider._generate("make a plan", {})

    assert calls == 1


@pytest.mark.asyncio
async def test_gemini_request_timeout_is_bounded(monkeypatch):
    async def generate_content(*, model, contents, config):
        await asyncio.sleep(10)

    provider = object.__new__(GeminiProvider)
    provider.model = "gemini-3.5-flash-lite"
    provider.fallback_model = None
    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    monkeypatch.setattr(gemini_module, "REQUEST_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        await provider._generate("make a plan", {})


@pytest.mark.asyncio
async def test_gemini_video_review_uses_frames_and_does_not_claim_sync_review():
    provider = object.__new__(GeminiProvider)
    captured = {}

    async def analyze_images(prompt, images, *, temperature=None):
        captured.update(prompt=prompt, images=images, temperature=temperature)
        return {"issues": []}

    provider.analyze_images = analyze_images
    result = await provider.analyze_video(
        "check narration timing",
        b"video bytes",
        b"audio bytes",
        keyframes=[b"frame 1", b"frame 2"],
        timestamp_offset_seconds=12,
        excerpt_duration_seconds=8,
    )

    assert result["review_mode"] == "keyframes_only"
    assert result["issues"] == []
    assert captured["images"] == [b"frame 1", b"frame 2"]
    assert "Do not report synchronization or timing issues" in captured["prompt"]
