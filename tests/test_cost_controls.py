"""Per-build cost metering, prompt caching of shared code-prompt text, planner model."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.config import Settings
from scholarmotion.observability.usage import (
    current_ledger,
    record_usage,
    track_usage,
    usage_stage,
)
from scholarmotion.providers import create_llm_provider, create_planner_provider
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.schemas import SceneSpec, VisualBeat


def _chat_response(prompt_tokens=1000, completion_tokens=200, cached=0, cost=None):
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": 50},
    }
    if cost is not None:
        usage["cost"] = cost
    return SimpleNamespace(usage=SimpleNamespace(**usage, model_extra={}))


def test_ledger_sums_reported_costs_per_stage_and_model():
    with track_usage() as ledger:
        with usage_stage("script"):
            record_usage(_chat_response(cost=0.01), purpose="structured", model="m/main")
        record_usage(_chat_response(cached=800, cost=0.002), purpose="code", model="m/main")
    assert current_ledger() is None
    summary = ledger.summary()
    assert summary["calls"] == 2
    assert summary["cost_usd"] == pytest.approx(0.012)
    assert summary["cached_tokens"] == 800
    assert {row["purpose"] for row in summary["by_purpose"]} == {"script:structured", "code"}


def test_missing_costs_are_estimated_from_price_list_with_cache_discount():
    with track_usage() as ledger:
        record_usage(_chat_response(1000, 100, cached=600), purpose="code", model="m/x")
    ledger.estimate_missing_costs(
        {"m/x": {"prompt": 3e-6, "completion": 15e-6, "input_cache_read": 0.3e-6}}
    )
    # 400 fresh * 3e-6 + 600 cached * 0.3e-6 + 100 * 15e-6
    assert ledger.summary()["cost_usd"] == pytest.approx(0.0012 + 0.00018 + 0.0015, abs=1e-4)


def test_usage_is_ignored_outside_a_tracked_operation():
    record_usage(_chat_response(cost=1.0), purpose="code", model="m")  # no error, no ledger


@pytest.mark.asyncio
async def test_concurrent_scene_tasks_share_the_build_ledger():
    async def call(index):
        record_usage(_chat_response(cost=0.001), purpose="code", model=f"m{index}")

    with track_usage() as ledger:
        await asyncio.gather(*(asyncio.create_task(call(i)) for i in range(4)))
    assert ledger.summary()["calls"] == 4


SPEC = SceneSpec(
    scene_id="S01",
    chapter_id="C01",
    title="Matrix addition",
    learning_objective="Add two matrices entry by entry",
    narration="We add matching entries. The result is a new matrix.",
    duration_target_seconds=8,
    visual_beats=[VisualBeat(beat_id="b", narration_segment="We add.", visual="Two matrices")],
)

CODE = (
    "from manim import *\nclass SceneS01(Scene):\n    def construct(self):\n        self.wait(1)\n"
)


@pytest.mark.asyncio
async def test_code_prompt_sends_shared_rules_and_template_as_cache_prefix():
    seen = {}

    class Provider:
        name = "openai_compatible"

        async def generate_code(self, prompt, *, cached_prefix=None, purpose="code"):
            seen.update(prompt=prompt, prefix=cached_prefix, purpose=purpose)
            return CODE

    await generate_scene_code(
        Provider(), SPEC, skill_context="SKILL TEXT", sentence_durations=[4.0, 4.0]
    )
    rules, template = seen["prefix"]
    assert "RELATIVE POSITIONING" in rules and "SKILL TEXT" in rules
    assert "WORKING TEMPLATE" in template
    # The per-scene part carries the narration, not the shared text.
    assert "We add matching entries" in seen["prompt"]
    assert "WORKING TEMPLATE" not in seen["prompt"]
    assert seen["purpose"] == "code"


@pytest.mark.asyncio
async def test_providers_without_cache_support_get_the_full_prompt():
    seen = {}

    class Provider:
        name = "openai_compatible"

        async def generate_code(self, prompt):
            seen["prompt"] = prompt
            return CODE

    await generate_scene_code(Provider(), SPEC, sentence_durations=[4.0, 4.0])
    assert "WORKING TEMPLATE" in seen["prompt"] and "We add matching entries" in seen["prompt"]


@pytest.mark.asyncio
async def test_openrouter_code_request_marks_prefix_blocks_cacheable():
    provider = OpenAICompatibleProvider(
        api_key="test", model="anthropic/x", base_url="https://openrouter.ai/api/v1"
    )
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="```python\n" + CODE + "```")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    provider.client = SimpleNamespace(
        base_url="https://openrouter.ai/api/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    await provider.generate_code("scene part", cached_prefix=["rules", "template"])
    content = captured["messages"][0]["content"]
    assert [block["text"] for block in content] == ["rules", "template", "scene part"]
    assert all(block.get("cache_control") == {"type": "ephemeral"} for block in content[:2])
    assert "cache_control" not in content[2]


def _settings(**overrides):
    return Settings(
        _env_file=None,
        main_llm_provider="openai_compatible",
        main_llm_model="anthropic/claude-sonnet-4.6",
        main_llm_base_url="https://openrouter.ai/api/v1",
        openrouter_api_key="test",
        **overrides,
    )


def test_planner_uses_cheaper_model_with_main_model_fallback():
    planner = create_planner_provider(
        _settings(planner_llm_model="anthropic/claude-haiku-5.5", planner_reasoning_effort="low")
    )
    assert planner.model == "anthropic/claude-haiku-5.5"
    assert planner.fallback_models == ["anthropic/claude-sonnet-4.6"]
    assert planner.structured_reasoning_effort == "low"


def test_planner_defaults_to_main_model_when_unset():
    settings = _settings()
    assert create_planner_provider(settings).model == create_llm_provider(settings).model
