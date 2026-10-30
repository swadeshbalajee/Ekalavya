"""Per-model thinking rules, image hand-off and checklist answers in the provider."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scholarmotion.config import Settings
from scholarmotion.providers.llm import openai_compatible
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.providers.llm.thinking import Thinking, parse_rules, thinking_for
from scholarmotion.verification.checklist import CHECKLIST, score_checklist

RULES = parse_rules("z-ai/glm-5.3-flash=6000, qwen/qwen3.8-omni-flash=off, qwen/=4000, x/y=high")


def test_exact_rule_beats_provider_prefix_and_unknown_models_keep_their_effort():
    assert thinking_for("z-ai/glm-5.3-flash", "medium", RULES) == Thinking(budget=6000)
    assert thinking_for("qwen/qwen3.8-omni-flash", "low", RULES) == Thinking(off=True)
    assert thinking_for("qwen/qwen3.8-flash", "low", RULES) == Thinking(budget=4000)
    assert thinking_for("x/y", "low", RULES) == Thinking(effort="high")
    # A rule for GLM-5.3 Flash is not a rule for GLM-5.3.
    assert thinking_for("z-ai/glm-5.3", "medium", RULES) == Thinking(effort="medium")


def test_a_budget_caps_thinking_and_leaves_room_for_the_answer():
    budget = Thinking(budget=6000)
    assert budget.reasoning() == {"max_tokens": 6000}
    assert budget.output_tokens(16000) == 22000
    assert Thinking(off=True).reasoning() == {"effort": "none"}
    assert Thinking(off=True).output_tokens(4096) == 4096
    assert Thinking(effort="low").reasoning() == {"effort": "low"}


def test_default_settings_cap_the_models_that_ran_away():
    rules = parse_rules(Settings().model_thinking)
    assert thinking_for("z-ai/glm-5.3-flash", "medium", rules).budget == 6000
    assert thinking_for("qwen/qwen3.8-omni-flash", "low", rules).off
    assert thinking_for("deepseek/deepseek-v4.1-flash", "medium", rules).budget == 4000
    assert thinking_for("anthropic/claude-sonnet-5.5", "medium", rules).effort == "medium"


def _provider(captured: list, rules=RULES) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model = "z-ai/glm-5.3"
    provider.fallback_models = ["z-ai/glm-5.3-flash"]
    provider.openrouter = True
    provider.name = "openai_compatible"
    provider.reasoning_effort = "medium"
    provider.code_max_output_tokens = 16000
    provider.code_timeout_seconds = 60
    provider.request_timeout_seconds = 90
    provider.default_temperature = 0.7
    provider.thinking_rules = rules

    async def create(**kwargs):
        captured.append(kwargs)
        if kwargs["model"] == "z-ai/glm-5.3":
            raise TimeoutError("slow")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="from manim import *\nclass S(Scene):\n    pass\n"
                    ),
                )
            ],
            usage=None,
        )

    async def models():
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="z-ai/glm-5.3", model_extra={"architecture": {"input_modalities": ["text"]}}
                ),
                SimpleNamespace(
                    id="z-ai/glm-5.3-flash",
                    model_extra={"architecture": {"input_modalities": ["text", "image"]}},
                ),
            ]
        )

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        models=SimpleNamespace(list=models),
    )
    return provider


@pytest.mark.asyncio
async def test_code_requests_follow_each_models_rule_and_images_go_only_to_models_that_see(
    monkeypatch,
):
    monkeypatch.setattr(openai_compatible, "_MODEL_INPUTS", None)
    captured: list = []
    await _provider(captured).generate_code("write it", cached_prefix=["rules"], images=[b"figure"])

    text_only, sighted = captured
    assert [part["type"] for part in text_only["messages"][0]["content"]] == ["text", "text"]
    assert text_only["extra_body"]["reasoning"] == {"effort": "medium"}
    assert text_only["max_tokens"] == 16000
    assert [part["type"] for part in sighted["messages"][0]["content"]] == [
        "text",
        "text",
        "image_url",
    ]
    assert sighted["extra_body"]["reasoning"] == {"max_tokens": 6000}
    assert sighted["max_tokens"] == 22000


@pytest.mark.asyncio
async def test_video_review_returns_checklist_answers_in_scene_time():
    captured: list = []
    provider = _provider(captured)
    provider.model, provider.fallback_models = "qwen/qwen3.8-omni-flash", []

    async def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"summary":"bars","checklist":['
                        '{"id":"text_overlap","pass":false,"timestamp_start_seconds":1,'
                        '"timestamp_end_seconds":2,"observed":"12.9 on the axis",'
                        '"concrete_code_change":"next_to(bar, UP)"},'
                        '{"id":"readable","pass":true}]}'
                    ),
                )
            ],
            usage=None,
        )

    provider.client.chat.completions.create = create
    result = await provider.analyze_video(
        "review", b"video", b"wav", timestamp_offset_seconds=10, excerpt_duration_seconds=8
    )

    assert captured[0]["extra_body"]["reasoning"] == {"effort": "none"}
    assert result["checklist"][0] == {
        "id": "text_overlap",
        "passed": False,
        "description": "12.9 on the axis at 11.0-12.0s",
        "suggested_repair": "next_to(bar, UP)",
    }
    assert result["checklist"][1]["passed"] is True


def _answers(*failed: str) -> list[dict]:
    return [
        {"id": item.id, "passed": item.id not in failed, "description": "", "suggested_repair": ""}
        for item in CHECKLIST
    ]


def test_checklist_needs_ninety_percent_and_every_must_pass_item():
    assert score_checklist("S", _answers()).passed
    assert score_checklist("S", _answers("readable")).passed  # 23/25
    assert not score_checklist("S", _answers("highlight_targets")).passed  # 22/25
    assert not score_checklist("S", _answers("readable", "not_crowded")).passed  # 22/25
    # A must-pass failure rejects the scene even though it scores 88%.
    result = score_checklist("S", _answers("in_frame"))
    assert not result.passed and result.failed == ["in_frame"]


def test_a_checklist_failure_of_only_minor_items_still_blocks():
    result = score_checklist("S", _answers("readable", "not_crowded"))
    assert any(issue.category == "video.checklist.score" for issue in result.issues)
    assert any(issue.severity == "high" for issue in result.issues)
