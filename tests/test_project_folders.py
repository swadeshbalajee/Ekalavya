from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api.main import app, copy_project, current_user_id, move_project
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, ProjectFolder, TutorMessage, UserAccount
from scholarmotion.schemas import ProjectCopyRequest, ProjectMoveRequest


def test_versioned_chat_message_edit_route_is_registered() -> None:
    operation = app.openapi()["paths"]["/v1/projects/{project_id}/chat/{message_id}"]
    assert "patch" in operation


@pytest.mark.asyncio
async def test_move_and_copy_chat_between_owned_project_folders():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(
            username="folder-owner",
            password_hash="unused",
            display_name="Folder Owner",
        )
        session.add(user)
        await session.flush()
        first = ProjectFolder(owner_id=user.id, name="First")
        second = ProjectFolder(owner_id=user.id, name="Second")
        session.add_all([first, second])
        await session.flush()
        chat = Project(
            owner_id=user.id,
            folder_id=first.id,
            title="Derivatives",
            request="Explain derivatives.",
            original_request="Explain derivatives.",
            mode="chat",
        )
        session.add(chat)
        await session.flush()
        session.add(
            TutorMessage(
                project_id=chat.id,
                role="user",
                canonical_text="Why?",
                display_text="Why?",
                language="English",
            )
        )
        await session.commit()

        context = current_user_id.set(user.id)
        try:
            moved = await move_project(chat.id, ProjectMoveRequest(folder_id=second.id), session)
            copied = await copy_project(
                chat.id,
                ProjectCopyRequest(folder_id=first.id, title="Derivatives copy"),
                session,
            )
        finally:
            current_user_id.reset(context)

        assert moved["folder_id"] == str(second.id)
        assert copied["folder_id"] == str(first.id)
        copied_messages = (
            await session.scalars(
                select(TutorMessage).where(TutorMessage.project_id == UUID(copied["id"]))
            )
        ).all()
        assert [message.display_text for message in copied_messages] == ["Why?"]
    await engine.dispose()
