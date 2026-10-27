from __future__ import annotations

from types import SimpleNamespace

import pytest

from scholarmotion.providers.llm.openai_compatible import (
    OpenAICompatibleProvider,
    _is_manim_code,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["medium", "high"])
async def test_code_generation_sends_configured_reasoning_effort(effort):
    from scholarmotion.config import Settings
    from scholarmotion.providers.factory import create_code_provider

    provider = create_code_provider(
        Settings(
            _env_file=None,
            code_provider="openai_compatible",
            code_api_key="test-key",
            code_model="anthropic/claude-sonnet-4.6",
            code_base_url="https://openrouter.ai/api/v1",
            code_reasoning_effort=effort,
            code_max_output_tokens=16000,
        )
    )
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="from manim import *\nclass SceneS01(Scene):\n    pass"
                    ),
                )
            ],
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    await provider.generate_code("Create a scene")
    assert captured["extra_body"]["reasoning"] == {"effort": effort}
    assert captured["model"] == "anthropic/claude-sonnet-4.6"
    assert captured["max_tokens"] == 16000


@pytest.mark.asyncio
async def test_openrouter_falls_back_when_primary_model_is_rate_limited():
    class RateLimitError(RuntimeError):
        status_code = 429

    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "primary/free"
    provider.fallback_models = ["backup/free"]
    calls: list[str] = []

    async def request(model: str):
        calls.append(model)
        if model == "primary/free":
            raise RateLimitError("rate limited")
        return SimpleNamespace(status="completed", error=None, model=model)

    result = await provider._with_fallback(request)

    assert result.model == "backup/free"
    assert calls == ["primary/free", "backup/free"]


@pytest.mark.asyncio
async def test_vision_converts_string_issues_to_verification_records():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "vision/free"
    provider.fallback_models = []
    provider.capabilities = SimpleNamespace(images=True)

    async def create(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"issues":["labels overlap"]}'))
            ]
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await provider.analyze_images("check this frame", [b"image"])

    assert result["passed"] is False
    assert result["issues"][0]["description"] == "labels overlap"
    assert result["issues"][0]["severity"] == "medium"


@pytest.mark.asyncio
async def test_code_generation_falls_back_when_model_returns_reasoning_instead_of_code():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "primary/free"
    provider.fallback_models = ["code/free"]
    calls: list[str] = []

    async def request(model: str):
        calls.append(model)
        output = (
            "Here's my thinking process"
            if model == "primary/free"
            else "from manim import *\nclass SceneS01(Scene):\n    pass"
        )
        return SimpleNamespace(status="completed", error=None, output_text=output, model=model)

    result = await provider._with_fallback(
        request,
        validator=lambda item: _is_manim_code(item.output_text),
        invalid_output_message="invalid generated Python",
    )

    assert result.model == "code/free"
    assert calls == ["primary/free", "code/free"]


@pytest.mark.asyncio
async def test_video_review_sends_paired_media_and_normalizes_absolute_issue_time():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "google/gemini-2.5-flash-lite"
    provider.fallback_models = []
    provider.openrouter = True
    provider.reasoning_effort = "low"
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            model=provider.model,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '```json\n{"summary":"Equation overlaps diagram", "issues":[{'
                            '"type":"unreadable_text", "timestamp":"00:01.5 - 00:03",'
                            '"description":"Equation covers and obscures the diagram label",'
                            '"concrete_code_change":"Move equation below the title"}]}\n```'
                        )
                    )
                )
            ],
            usage=SimpleNamespace(
                cost=0.001, completion_tokens_details=SimpleNamespace(reasoning_tokens=20)
            ),
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await provider.analyze_video(
        "review this clip", b"video", b"wav", keyframes=[b"frame"], timestamp_offset_seconds=8
    )

    content = captured["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "video_url", "input_audio", "image_url"]
    assert captured["reasoning_effort"] == "low"
    assert captured["response_format"] == {"type": "json_object"}
    assert result["issues"][0]["category"] == "video.overlap"
    assert result["issues"][0]["severity"] == "high"
    assert result["issues"][0]["category"] == "video.overlap"
    assert "9.5-11.0s" in result["issues"][0]["description"]
    assert result["issues"][0]["suggested_repair"] == "Move equation below the title"


@pytest.mark.asyncio
async def test_video_review_uses_configured_fallback_when_primary_json_is_invalid():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "google/gemini-2.5-flash-lite"
    provider.fallback_models = ["google/gemini-2.5-flash"]
    provider.openrouter = True
    provider.reasoning_effort = "low"
    models = []

    async def create(**kwargs):
        model = kwargs["model"]
        models.append(model)
        content = "not json" if model == provider.model else '{"issues":[]}'
        return SimpleNamespace(
            model=model,
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(cost=0, completion_tokens_details=None),
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = await provider.analyze_video("review", b"video", b"wav")

    assert models == ["google/gemini-2.5-flash-lite", "google/gemini-2.5-flash"]
    assert result["issues"] == []


@pytest.mark.asyncio
async def test_video_review_discards_timestamps_outside_the_supplied_excerpt():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "google/gemini-2.5-flash-lite"
    provider.fallback_models = []
    provider.openrouter = True
    provider.reasoning_effort = "low"

    async def create(**_kwargs):
        return SimpleNamespace(
            model=provider.model,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"issues":[{"category":"overlap","timestamp_start_seconds":12,'
                            '"timestamp_end_seconds":15,"observed":"Overlap"}]}'
                        )
                    )
                )
            ],
            usage=SimpleNamespace(cost=0, completion_tokens_details=None),
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await provider.analyze_video(
        "review", b"video", b"wav", timestamp_offset_seconds=20, excerpt_duration_seconds=8
    )

    assert result["issues"] == []


@pytest.mark.asyncio
async def test_video_balance_floor_falls_back_to_frame_only_review():
    class BalanceFloorError(RuntimeError):
        status_code = 402

    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "google/gemini-3-flash-preview"
    provider.fallback_models = []
    provider.openrouter = True
    provider.reasoning_effort = "low"
    provider.client = SimpleNamespace()

    async def unavailable(_request, **_kwargs):
        raise BalanceFloorError("This request requires at least $1.00 in balance for video")

    captured = {}

    async def inspect_images(prompt, images, *, temperature=None):
        captured.update(prompt=prompt, images=images, temperature=temperature)
        return {
            "issues": [
                {
                    "category": "vision.readability",
                    "severity": "medium",
                    "confidence": 0.8,
                    "frames": [1],
                    "objects": ["equation"],
                    "description": "The equation is clipped.",
                    "suggested_repair": "Move the equation up.",
                }
            ]
        }

    provider._with_fallback = unavailable
    provider.analyze_images = inspect_images
    result = await provider.analyze_video(
        "review synchronized media", b"video", b"audio", keyframes=[b"frame"]
    )

    assert result["review_mode"] == "keyframes_only"
    assert result["issues"][0]["category"] == "video.frame_visual.vision.readability"
    assert result["issues"][0]["frames"] == [1]
    assert "Do not report synchronization" in captured["prompt"]
    assert captured["images"] == [b"frame"]


@pytest.mark.asyncio
async def test_video_service_errors_without_balance_floor_do_not_silently_downgrade():
    class ProviderUnavailable(RuntimeError):
        status_code = 503

    provider = object.__new__(OpenAICompatibleProvider)
    provider.model = "google/gemini-3-flash-preview"
    provider.fallback_models = []
    provider.openrouter = True
    provider.reasoning_effort = "low"

    async def unavailable(_request, **_kwargs):
        raise ProviderUnavailable("temporarily unavailable")

    provider._with_fallback = unavailable
    provider.analyze_images = lambda *_args, **_kwargs: pytest.fail(
        "generic provider outage must remain visible"
    )

    with pytest.raises(ProviderUnavailable):
        await provider.analyze_video("review", b"video", b"audio", keyframes=[b"frame"])
