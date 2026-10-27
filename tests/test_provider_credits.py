"""The sidebar balance comes from the API; the provider key never leaves the server."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from scholarmotion.api import main


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    async def user(_session, token):
        return ("user",) if token == "good" else None

    monkeypatch.setattr(main, "_user_from_token", user)
    monkeypatch.setattr(main.settings, "openrouter_api_key", "sk-or-test")
    monkeypatch.setattr(main.settings, "main_llm_base_url", "https://openrouter.ai/api/v1")
    main._credit_cache.clear()


@pytest.mark.asyncio
async def test_credit_is_reported_without_the_key_and_cached(monkeypatch):
    calls = []

    async def credit(key):
        calls.append(key)
        return {"remaining_usd": 7.25, "limit_usd": 70, "used_usd": 62.75}

    monkeypatch.setattr(main, "_openrouter_credit", credit)
    first = await main.provider_credits(None, "Bearer good")
    second = await main.provider_credits(None, "Bearer good")
    assert first["remaining_usd"] == 7.25 and first["available"] is True
    assert "sk-or-test" not in str(first)
    assert calls == ["sk-or-test"]  # the second read is served from the cache
    assert second == first


@pytest.mark.asyncio
async def test_credit_requires_a_signed_in_user():
    with pytest.raises(HTTPException) as error:
        await main.provider_credits(None, "Bearer bad")
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_non_openrouter_deployments_hide_the_balance(monkeypatch):
    monkeypatch.setattr(main.settings, "openrouter_api_key", None)
    monkeypatch.setattr(main.settings, "main_llm_api_key", "other-key")
    monkeypatch.setattr(main.settings, "main_llm_base_url", "https://api.example.com/v1")
    assert await main.provider_credits(None, "Bearer good") == {"available": False}
