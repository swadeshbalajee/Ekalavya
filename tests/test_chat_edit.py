from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api import main as api_main
from scholarmotion.api import tutor as tutor_api
from scholarmotion.api.tutor import TutorDecision, _fallback_tutor_answer
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import (
    Project,
    SourceChunk,
    SourceDocument,
    TutorMessage,
    UserAccount,
)
from scholarmotion.schemas import TutorChatRequest, TutorMessageEdit


@pytest.mark.asyncio
async def test_editing_a_message_keeps_its_old_replies_as_an_older_branch(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(
            username="chat-editor",
            password_hash="unused",
            display_name="Chat Editor",
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            title="Fractions",
            request="Explain fractions.",
            original_request="Explain fractions.",
            mode="chat",
        )
        session.add(project)
        await session.flush()
        start = datetime.now(UTC)
        original_messages = [
            ("user", "Explain fractions."),
            ("assistant", "A fraction is part of a whole."),
            ("user", "What is a numerator?"),
            ("assistant", "The numerator is on top."),
            ("user", "Give me a quiz."),
            ("assistant", "What is 1/2 + 1/4?"),
        ]
        stored = [
            TutorMessage(
                project_id=project.id,
                role=role,
                canonical_text=text,
                display_text=text,
                language="English",
                created_at=start + timedelta(microseconds=index),
            )
            for index, (role, text) in enumerate(original_messages)
        ]
        session.add_all(stored)
        await session.commit()

        async def reply(_session, _settings, _project, payload):
            assert payload.message == "Explain the numerator again."
            _session.add_all(
                [
                    TutorMessage(
                        project_id=_project.id,
                        role="user",
                        canonical_text=payload.message,
                        display_text=payload.message,
                        language=payload.language or "English",
                    ),
                    TutorMessage(
                        project_id=_project.id,
                        role="assistant",
                        canonical_text="The numerator tells how many parts we have.",
                        display_text="The numerator tells how many parts we have.",
                        language="English",
                    ),
                ]
            )
            return TutorDecision(
                "The numerator tells how many parts we have.",
                "The numerator tells how many parts we have.",
                "answer",
            )

        monkeypatch.setattr(api_main, "tutor_respond", reply)
        context = api_main.current_user_id.set(user.id)
        try:
            response = await api_main.edit_tutor_message(
                project.id,
                stored[2].id,
                TutorMessageEdit(message="Explain the numerator again."),
                session,
            )
        finally:
            api_main.current_user_id.reset(context)

        assert response.message == "The numerator tells how many parts we have."
        context = api_main.current_user_id.set(user.id)
        try:
            shown = await api_main.tutor_messages(project.id, session)
        finally:
            api_main.current_user_id.reset(context)
        # Only the active branch is shown ...
        assert [(item["role"], item["message"]) for item in shown] == [
            ("user", "Explain fractions."),
            ("assistant", "A fraction is part of a whole."),
            ("user", "Explain the numerator again."),
            ("assistant", "The numerator tells how many parts we have."),
        ]
        # ... and the replaced turn with its old continuation is kept as branch 1 of 2.
        edited = shown[2]
        assert edited["branch_index"] == 2
        assert [branch["index"] for branch in edited["branches"]] == [1]
        assert [m["message"] for m in edited["branches"][0]["messages"]] == [
            "What is a numerator?",
            "The numerator is on top.",
            "Give me a quiz.",
            "What is 1/2 + 1/4?",
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_tutor_prompt_includes_recent_persisted_turns(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Translator:
        def __init__(self, _provider, _machine=None):
            pass

        async def to_english(self, text, source_language=None):
            return text

        async def translate(self, text, source_language=None, target_language=None):
            return text

    class CapturingProvider:
        name = "openai_compatible"

        async def generate_text(self, prompt, *, temperature=None):
            self.prompt = prompt
            return "Because it determines how much field passes through the surface."

    provider = CapturingProvider()
    monkeypatch.setattr(tutor_api, "TranslationService", Translator)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: provider)

    async with sessions() as session:
        user = UserAccount(
            username="chat-history-tester",
            password_hash="unused",
            display_name="History Tester",
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            title="Flux tutor",
            request="Teach magnetic flux.",
            mode="chat",
        )
        session.add(project)
        await session.flush()
        paper = SourceDocument(
            project_id=project.id,
            kind="paper",
            title="Magnetic Flux Study",
            uri="paper.pdf",
            metadata_json={},
        )
        session.add(paper)
        await session.flush()
        session.add(
            SourceChunk(
                document_id=paper.id,
                section="2.1 Flux through a surface",
                page=4,
                content_type="paragraph",
                text="Magnetic flux is the surface integral of the magnetic field dotted with the area normal.",
            )
        )
        session.add_all(
            [
                TutorMessage(
                    project_id=project.id,
                    role="user",
                    canonical_text="Define magnetic flux.",
                    display_text="Define magnetic flux.",
                    language="English",
                ),
                TutorMessage(
                    project_id=project.id,
                    role="assistant",
                    canonical_text="It measures field passing through a surface.",
                    display_text="It measures field passing through a surface.",
                    language="English",
                ),
            ]
        )
        await session.flush()

        response = await tutor_api.respond(
            session,
            Settings(main_llm_provider="openai_compatible"),
            project,
            TutorChatRequest(message="Why does that matter?"),
        )

        assert response.action == "answer"
        assert "It measures field passing through a surface." in provider.prompt
        assert "CURRENT LEARNER MESSAGE:\nWhy does that matter?" in provider.prompt
        assert "Magnetic Flux Study, p. 4" in provider.prompt
        assert "surface integral of the magnetic field" in provider.prompt

    await engine.dispose()


def test_offline_tutor_fallback_quotes_the_retrieved_passage_with_its_citation():
    project = Project(
        title="Attention Is All You Need paper video",
        request="Explain self-attention with a Q, K, V worked example.",
    )
    answer = _fallback_tutor_answer(
        project,
        "What do Q, K, and V mean?",
        "[Attention Is All You Need, p. 4] Scaled dot-product attention is defined as "
        "softmax(QK^T / sqrt(d_k))V.",
    )

    # No topic-specific canned text: the fallback grounds itself in what was retrieved.
    assert "Scaled dot-product attention is defined as" in answer
    assert "[Attention Is All You Need, p. 4]" in answer
