"""A finished video replaces the failure notices of the attempts before it."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api.service import clear_superseded_failures
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, TutorMessage, UserAccount

START = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)


async def test_only_failures_since_the_last_finished_video_are_removed():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        learner = UserAccount(username="learner", password_hash="x", display_name="Learner")
        session.add(learner)
        await session.flush()
        chat = Project(owner_id=learner.id, title="Paper", request="r", status="GENERATING")
        session.add(chat)
        await session.flush()

        def notice(minutes, action, text):
            return TutorMessage(
                project_id=chat.id,
                role="assistant",
                canonical_text=text,
                display_text=text,
                action=action,
                created_at=START + timedelta(minutes=minutes),
            )

        session.add_all(
            [
                notice(0, "verification_failed", "first lecture failed"),
                notice(10, "verification_complete", "lecture ready"),
                notice(20, "verification_failed", "edit failed"),
                notice(30, "verification_failed", "edit failed again"),
                notice(31, "answer", "a normal reply"),
            ]
        )
        await session.commit()

        await clear_superseded_failures(session, chat.id)
        await session.commit()
        kept = (
            await session.scalars(
                select(TutorMessage.canonical_text).order_by(TutorMessage.created_at)
            )
        ).all()
    await engine.dispose()
    # Only the failures the new video replaces are removed (an earlier video
    # already replaced the ones before it); ordinary replies stay.
    assert kept == ["first lecture failed", "lecture ready", "a normal reply"]
