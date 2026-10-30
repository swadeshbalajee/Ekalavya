import asyncio
from types import SimpleNamespace

import pytest

from scholarmotion.providers.llm.base import call_timeout, call_timeout_seconds
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider


def test_a_stage_block_only_lengthens_the_limit():
    assert call_timeout_seconds(90) == 90
    with call_timeout(420):
        assert call_timeout_seconds(90) == 420
        assert call_timeout_seconds(600) == 600
    assert call_timeout_seconds(90) == 90


def _provider():
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.model = "slow/model"
    provider.fallback_models = []
    provider.request_timeout_seconds = 0.05
    provider.openrouter = False
    provider.name = "openai_compatible"
    return provider


@pytest.mark.asyncio
async def test_a_timed_out_request_names_the_model_and_limit():
    async def slow(model):
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError, match=r"slow/model did not answer within 0 seconds"):
        await _provider()._with_fallback(slow, timeout_seconds=0.05)


@pytest.mark.asyncio
async def test_a_long_stage_lets_a_slow_answer_finish():
    async def slow(model):
        await asyncio.sleep(0.2)
        return SimpleNamespace(status=None, error=None, choices=None, usage=None)

    with call_timeout(2):
        response = await _provider()._with_fallback(slow, timeout_seconds=0.05)
    assert response.error is None
