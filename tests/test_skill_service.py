from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.persistence.database import Base
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge
from scholarmotion.skills.service import SkillService


@pytest.mark.asyncio
async def test_skill_progressive_disclosure_and_versioning():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await bootstrap_system_knowledge(session)
        await session.commit()
        skills = SkillService(session)
        catalog = await skills.catalog()
        assert any(item.name == "manim-scene" for item in catalog)
        context = await skills.context_for("code")
        assert "<available_skills>" in context
        assert '<skill name="manim-scene"' in context

        draft = await skills.create_draft(
            name="diagram-review",
            description="Review visual lesson diagrams when a scene has an unresolved visual issue.",
            instructions="Return only evidence-backed visual issues.",
            manifest={"capabilities": ["vision-verification"], "allowed_tools": ["vision_verify"]},
            resources=[{"path": "references/checklist.md", "content": "Check labels and arrows."}],
        )
        await skills.activate(draft.name, draft.version)
        replacement = await skills.create_draft(
            name="diagram-review",
            description="Review visual lesson diagrams using the updated approved checklist.",
            instructions="Report only reproducible, evidence-backed visual issues.",
            manifest={"capabilities": ["vision-verification"], "allowed_tools": ["vision_verify"]},
        )
        await skills.activate(replacement.name, replacement.version)
        await session.commit()
        loaded = await skills.load("diagram-review")
        assert loaded.version == 2
        assert loaded.as_skill_md().startswith("---\nname: diagram-review")
        assert loaded.resources == ()
    await engine.dispose()
