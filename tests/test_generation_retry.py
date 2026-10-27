from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api.service import _save_preparation_artifact, reset_failed_generation
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import (
    Chapter,
    Project,
    ProjectArtifact,
    RenderArtifact,
    Scene,
    SceneVersion,
    TutorMessage,
)


@pytest.mark.asyncio
async def test_retry_updates_preparation_artifact_instead_of_violating_unique_key():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    async with sessions() as session:
        session.add(Project(id=project_id, title="Retry", request="Retry this lesson"))
        await session.commit()

        await _save_preparation_artifact(session, project_id, {"attempt": 1})
        await session.commit()
        await _save_preparation_artifact(session, project_id, {"attempt": 2})
        await session.commit()

        rows = (
            await session.scalars(
                select(ProjectArtifact).where(ProjectArtifact.project_id == project_id)
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].payload == {"attempt": 2}
        assert rows[0].active is True
        assert rows[0].stale is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_generation_retry_removes_partial_derived_rows_and_keeps_chat():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    async with sessions() as session:
        project = Project(
            id=project_id,
            title="Retry partial video",
            request="Explain the four Maxwell equations.",
            status="FAILED",
            active_video_path="projects/partial.mp4",
        )
        session.add(project)
        await session.flush()
        chapter = Chapter(project_id=project_id, chapter_key="C01", title="Maxwell", order_index=1)
        session.add(chapter)
        await session.flush()
        scene = Scene(
            project_id=project_id,
            chapter_id=chapter.id,
            scene_key="S01",
            title="Gauss law",
            order_index=1,
        )
        session.add(scene)
        await session.flush()
        session.add_all(
            [
                SceneVersion(scene_id=scene.id, version=1, reason="initial"),
                RenderArtifact(
                    scene_id=scene.id,
                    version=1,
                    code_version=1,
                    audio_version=1,
                    uri="projects/partial.mp4",
                ),
                ProjectArtifact(
                    project_id=project_id,
                    kind="video",
                    version=1,
                    uri="projects/partial.mp4",
                ),
                TutorMessage(
                    project_id=project_id,
                    role="user",
                    canonical_text="Explain the four Maxwell equations.",
                    display_text="Explain the four Maxwell equations.",
                    language="English",
                ),
            ]
        )
        await session.flush()

        assert await reset_failed_generation(session, project) is True
        await session.commit()

        assert await session.scalar(select(Scene.id).where(Scene.project_id == project_id)) is None
        assert (
            await session.scalar(
                select(ProjectArtifact.id).where(ProjectArtifact.project_id == project_id)
            )
            is None
        )
        assert (
            await session.scalar(
                select(TutorMessage.id).where(TutorMessage.project_id == project_id)
            )
            is not None
        )
        assert project.active_video_path is None
    await engine.dispose()
