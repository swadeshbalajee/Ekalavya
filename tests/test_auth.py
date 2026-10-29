from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api import main as api_main
from scholarmotion.api.main import (
    _hash_password,
    _issue_session,
    _password_matches,
    _user_from_token,
    create_project,
    current_user_id,
)
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, UserAccount
from scholarmotion.schemas import ProjectCreate


@pytest.fixture(autouse=True)
def offline_translation(monkeypatch):
    """New chats translate their request to English; keep that off the network."""
    monkeypatch.setattr(
        api_main, "create_translation_provider", lambda _s: SimpleNamespace(name="mock")
    )
    monkeypatch.setattr(api_main, "create_machine_translator", lambda _s: None)


def test_passwords_are_salted_and_verified_with_scrypt():
    first = _hash_password("correct horse battery staple")
    second = _hash_password("correct horse battery staple")
    assert first.startswith("scrypt$")
    assert first != second
    assert _password_matches("correct horse battery staple", first)
    assert not _password_matches("wrong password", first)


@pytest.mark.asyncio
async def test_issued_login_session_can_be_resolved_and_revoked():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(
            username="learner",
            password_hash=_hash_password("password123"),
            display_name="Learner",
        )
        session.add(user)
        await session.flush()
        token = await _issue_session(session, user)
        await session.flush()
        authenticated = await _user_from_token(session, token)
        assert authenticated is not None
        assert authenticated[0].id == user.id
        authenticated[1].revoked = True
        await session.flush()
        assert await _user_from_token(session, token) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_project_inherits_account_language_defaults_and_allows_overrides():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(
            username="tamil_learner",
            password_hash="unused",
            display_name="Tamil Learner",
            default_text_language="Tamil",
            default_voice_language="Tamil",
        )
        session.add(user)
        await session.flush()
        context = current_user_id.set(user.id)
        try:
            inherited = await create_project(
                ProjectCreate(title="Tamil defaults", request="Explain Gauss's law."),
                session,
            )
            explicit = await create_project(
                ProjectCreate(
                    title="Language overrides",
                    request="Explain Gauss's law.",
                    text_language="Hindi",
                    voice_language="Tamil",
                ),
                session,
            )
        finally:
            current_user_id.reset(context)
        rows = list(
            await session.scalars(
                select(Project).where(Project.owner_id == user.id).order_by(Project.created_at)
            )
        )
        assert len(rows) == 2
        assert (rows[0].text_language, rows[0].voice_language) == ("Tamil", "Tamil")
        assert (rows[1].text_language, rows[1].voice_language) == ("Hindi", "Tamil")
        assert inherited["text_language"] == "Tamil"
        assert explicit["text_language"] == "Hindi"
    await engine.dispose()


@pytest.mark.asyncio
async def test_project_duration_uses_account_default_unless_prompt_is_more_specific():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(
            username="duration_learner",
            password_hash="unused",
            display_name="Duration Learner",
            default_duration_minutes=7,
        )
        session.add(user)
        await session.flush()
        context = current_user_id.set(user.id)
        try:
            default_project = await create_project(
                ProjectCreate(title="Default", request="Explain electric flux."), session
            )
            prompt_project = await create_project(
                ProjectCreate(
                    title="Prompt wins",
                    request="Create a 12-minute video explaining electric flux.",
                    target_duration_minutes=5,
                ),
                session,
            )
        finally:
            current_user_id.reset(context)
        assert default_project["target_duration_minutes"] == 7
        # The form's value is used here; a length stated in chat is the planner's call.
        assert prompt_project["target_duration_minutes"] == 5
    await engine.dispose()
