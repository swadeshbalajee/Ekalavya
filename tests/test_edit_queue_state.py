"""A queued edit must not appear complete while its previous video is active."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api import main
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, UserAccount
from scholarmotion.schemas import EditRangeRequest


@pytest.mark.asyncio
async def test_queueing_edit_marks_project_editing_and_keeps_previous_video(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def start(*args):
        return "queued-edit-" + str(uuid4()), str(uuid4())

    monkeypatch.setattr(main, "start_edit_workflow", start)
    async with sessions() as session:
        user = UserAccount(username="edit-queue-test", password_hash="unused", display_name="QA")
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            title="QA lesson",
            request="Explain force",
            status="COMPLETE",
            active_video_path="previous-good.mp4",
        )
        session.add(project)
        await session.flush()
        token = main.current_user_id.set(user.id)
        try:
            response = await main.edit_range(
                project.id,
                EditRangeRequest(start_time=0, end_time=5, instruction="Clarify"),
                session,
            )
        finally:
            main.current_user_id.reset(token)
        await session.refresh(project)
        assert response["status"] == "queued"
        assert project.status == "EDITING"
        assert project.active_video_path == "previous-good.mp4"
    await engine.dispose()
