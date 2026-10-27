"""Browser media requests may cross UI/API ports while authentication still applies."""

import httpx
import pytest

from scholarmotion.api import main


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("http://127.0.0.1:8501", True),
        ("http://localhost:8501", True),
        ("https://untrusted.example", False),
    ],
)
async def test_only_configured_ui_origins_receive_media_cors_headers(origin, allowed):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)


@pytest.mark.asyncio
async def test_allowed_ui_origin_still_requires_project_authentication(monkeypatch):
    monkeypatch.setattr(main.settings, "app_api_key", "test-api-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/projects", headers={"Origin": "http://127.0.0.1:8501"})
    assert response.status_code == 401
