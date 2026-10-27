from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api.capabilities import create_capability_router
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import AdministrationAudit
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge


@pytest.mark.asyncio
async def test_skills_api_supports_catalog_draft_and_activation(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await bootstrap_system_knowledge(session)
        await session.commit()

    async def session_dependency():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(
        create_capability_router(
            Settings(environment="production", admin_api_key="reviewer-key"),
            LocalObjectStore(tmp_path),
            session_dependency,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        catalog = await client.get("/v1/skills")
        assert catalog.status_code == 200
        assert any(skill["name"] == "manim-scene" for skill in catalog.json())

        created = await client.post(
            "/v1/skills",
            json={
                "name": "diagram-review",
                "description": "Review diagrams for visual lesson defects.",
                "instructions": "Return only evidence-backed visual issues.",
                "capabilities": ["vision-verification"],
                "allowed_tools": ["vision_verify"],
                "resources": [
                    {
                        "path": "references/checklist.md",
                        "content": "Check labels and arrows.",
                    }
                ],
            },
        )
        assert created.status_code == 201
        assert created.json()["version"] == 1

        activated = await client.post("/v1/skills/diagram-review/versions/1/activate")
        assert activated.status_code == 403
        activated = await client.post(
            "/v1/skills/diagram-review/versions/1/activate",
            headers={"X-Admin-Key": "reviewer-key"},
        )
        assert activated.status_code == 200
        loaded = await client.get("/v1/skills/diagram-review")
        assert loaded.status_code == 200
        assert loaded.json()["skill_md"].startswith("---\nname: diagram-review")
        resource = await client.get("/v1/skills/diagram-review/resources/references/checklist.md")
        assert resource.json()["content"] == "Check labels and arrows."
    async with sessions() as session:
        audit = await session.scalar(
            select(AdministrationAudit).where(AdministrationAudit.action == "skill.activate")
        )
        assert audit is not None
        assert audit.target_id == "diagram-review:v1"
    await engine.dispose()
