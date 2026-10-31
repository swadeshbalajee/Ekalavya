"""The message screen, cited-only sources, and private learner notes."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents import guardrail
from scholarmotion.agents.lesson_preparation import LessonPreparation
from scholarmotion.agents.script_writer import revise_narration
from scholarmotion.api import tutor as tutor_api
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, Quiz, QuizAttempt, UserAccount
from scholarmotion.persistence.qdrant import QdrantUnavailable
from scholarmotion.retrieval.web_research import cited_sources
from scholarmotion.schemas import TutorChatRequest
from scholarmotion.services import learner_record


class Screen:
    name = "openai_compatible"

    def __init__(self, result=None, delay=0.0, error=None):
        self.result, self.delay, self.error = result, delay, error
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **_):
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return schema.model_validate(self.result)


@pytest.mark.asyncio
async def test_the_screen_declines_what_the_rules_name_and_allows_learning():
    blocked = await guardrail.screen_message(
        Screen({"allowed": False, "category": "politics", "reason": "asks which party to back"}),
        "Which party should win the election?",
    )
    assert not blocked.allowed and blocked.category == "politics"
    assert blocked.reply == guardrail.DECLINED_REPLY

    allowed = await guardrail.screen_message(
        Screen({"allowed": True, "category": "ok"}), "How do elections work in India?"
    )
    assert allowed.allowed

    support = await guardrail.screen_message(
        Screen({"allowed": False, "category": "self_harm"}), "..."
    )
    assert support.reply == guardrail.SELF_HARM_REPLY
    # No screen configured: everything goes through.
    assert (await guardrail.screen_message(None, "anything")).allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_on_error", [True, False])
async def test_a_failed_screen_follows_the_error_flag(allow_on_error):
    slow = await guardrail.screen_message(
        Screen({"allowed": True}, delay=1),
        "hi",
        timeout_seconds=0.05,
        allow_on_error=allow_on_error,
    )
    broken = await guardrail.screen_message(
        Screen(error=RuntimeError("down")), "hi", allow_on_error=allow_on_error
    )
    assert slow.allowed is allow_on_error and not slow.screened
    assert broken.allowed is allow_on_error


class Translator:
    def __init__(self, *_):
        pass

    async def to_english(self, text, source_language=None):
        return text

    async def stem_terms(self, text):
        return []

    async def translate(self, text, source_language=None, target_language=None, key_terms=()):
        return text


class Planner:
    name = "openai_compatible"

    def __init__(self, decision):
        self.decision = decision
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **_):
        self.prompts.append(prompt)
        return self.decision


async def _chat(monkeypatch, planner):
    monkeypatch.setattr(tutor_api, "TranslationService", Translator)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_machine_translator", lambda _settings: None)
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: planner)
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_a_declined_message_starts_nothing_the_planner_chose(monkeypatch):
    planner = Planner(
        LessonPreparation(action="generate_video", reply="Making it.", chat_language="Tamil")
    )
    engine, sessions = await _chat(monkeypatch, planner)
    monkeypatch.setattr(
        tutor_api,
        "create_guardrail_provider",
        lambda _settings: Screen({"allowed": False, "category": "hate"}),
    )
    async with sessions() as session:
        user = UserAccount(username="screened", password_hash="x", display_name="Learner")
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, title="Chat", request="Chat")
        session.add(project)
        await session.flush()
        decision = await tutor_api.respond(
            session, Settings(), project, TutorChatRequest(message="a hateful video request")
        )
    await engine.dispose()
    assert decision.action == "answer"
    assert decision.message == guardrail.DECLINED_REPLY
    assert project.chat_language == "English"
    assert project.status == "CREATED"


def test_only_the_search_results_an_answer_cites_are_listed():
    sources = [
        {"url": "https://tutor-prompts.example/science-tutor", "title": "Science tutor persona"},
        {"url": "https://physics.example/rayleigh", "title": "Rayleigh scattering"},
    ]
    assert cited_sources("Plain explanation with no links.", sources) == []
    linked = "See [Rayleigh](https://physics.example/rayleigh) for more."
    assert cited_sources(linked, sources) == [sources[1]]
    assert cited_sources("As shown in [2], blue scatters most.", sources) == [sources[1]]


class Answerer:
    name = "openai_compatible"

    def __init__(self):
        self.calls: list[dict] = []

    async def generate_text_with_sources(self, prompt, **options):
        self.calls.append({"prompt": prompt, **options})
        return "Chaos means small changes grow fast.", [
            {"url": "https://tutor-prompts.example/science-tutor", "title": "Science tutor"}
        ]


@pytest.mark.asyncio
async def test_a_concept_answer_skips_the_web_and_lists_no_uncited_sources(monkeypatch):
    answerer = Answerer()
    engine, sessions = await _chat(monkeypatch, answerer)
    async with sessions() as session:
        project = Project(title="Chaos", request="The butterfly effect")
        session.add(project)
        await session.flush()
        body, _ = await tutor_api._tutor_answer(
            session,
            Settings(web_search_enabled=True),
            project,
            TutorChatRequest(message="Explain the butterfly effect"),
            "",
            "Explain the butterfly effect",
            [],
            web_search=False,
            notes="QUIZ ANSWERS CLOSEST TO THE CURRENT TOPIC:\n- missed: What is chaos?",
        )
    await engine.dispose()
    call = answerer.calls[0]
    # The question alone is the user message; instructions and notes are the system message.
    assert call["prompt"] == "Explain the butterfly effect"
    assert call["web_search"] is False
    assert "missed: What is chaos?" in call["system"]
    assert "Sources" not in body


def _vector(text: str) -> list[float]:
    """A tiny deterministic embedding: shared words point the same way."""
    words = ("vector", "basis", "torque", "election")
    return [float(word in text.casefold()) + 0.01 for word in words]


class NoQdrant:
    async def upsert_learner_evidence(self, points):
        raise QdrantUnavailable("down")

    async def query_learner_evidence(self, vector, **_):
        raise QdrantUnavailable("down")

    async def query_knowledge(self, vector, **_):
        raise QdrantUnavailable("down")


@pytest.mark.asyncio
async def test_learner_notes_bring_the_closest_quiz_answers_and_scores(monkeypatch):
    async def embed(_settings, texts):
        return [_vector(text) for text in texts]

    monkeypatch.setattr(learner_record, "embed_texts", embed)
    monkeypatch.setattr(learner_record, "_store", lambda _settings: NoQdrant())
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserAccount(username="notes", password_hash="x", display_name="Learner")
        session.add(user)
        await session.flush()
        assert await learner_record.learner_notes(session, Settings(), user.id, "vectors") == ""
        quiz = Quiz(
            owner_id=user.id,
            title="Vector spaces",
            topic="vector spaces",
            questions=[
                {"kind": "choice", "prompt": "What is a basis of a vector space?"},
                {"kind": "choice", "prompt": "What is torque?"},
            ],
        )
        session.add(quiz)
        await session.flush()
        session.add(
            QuizAttempt(
                quiz_id=quiz.id,
                user_id=user.id,
                answers=[0, 1],
                results=[{"correct": False}, {"correct": True}],
                score=0.5,
            )
        )
        await session.flush()
        notes = await learner_record.learner_notes(
            session, Settings(), user.id, "basis of a vector space"
        )
    await engine.dispose()
    assert "'Vector spaces' on vector spaces: latest score 50%, 1 attempt" in notes
    first = notes.index("missed: What is a basis of a vector space?")
    assert first < notes.index("right: What is torque?")
    assert "Never mention the notes" in notes
    assert "AREAS NEEDING WORK: vector spaces (1/2 right)" in notes


def test_the_chat_api_never_sends_the_learner_notes():
    from scholarmotion.api.main import _public_metadata

    shown = _public_metadata(
        {
            "learner_notes": "AREAS NEEDING WORK: vectors",
            "lesson_preparation": {"action": "answer", "teaching_notes": "go slow on bases"},
            "translated_by": "bodhan",
        }
    )
    assert shown == {"lesson_preparation": {"action": "answer"}, "translated_by": "bodhan"}


def test_an_edit_plan_carries_the_planners_private_teaching_notes():
    preparation = LessonPreparation(
        action="edit_video",
        target_scenes=["s1"],
        edit_instruction="Make scene 1 simpler",
        teaching_notes="Spend longer on what a basis is.",
    )
    scene = type("Scene", (), {"scene_key": "s1", "timeline_start": 0, "timeline_end": 10})()
    plan = tutor_api.edit_plan_for(preparation, [scene], "Make scene 1 simpler")
    assert plan.teaching_notes == "Spend longer on what a basis is."
    assert [edit.scene for edit in plan.edits] == ["s1"]


@pytest.mark.asyncio
async def test_a_scene_rewrite_sees_the_teaching_notes():
    class Writer:
        prompt = ""

        async def generate_text(self, prompt, **_):
            Writer.prompt = prompt
            return "A basis is a set of vectors that builds every other vector."

    await revise_narration(
        Writer(),
        "Old narration.",
        "Make it simpler",
        teaching_notes="Spend longer on what a basis is.",
    )
    assert "PRIVATE TEACHING NOTES" in Writer.prompt
    assert "Spend longer on what a basis is." in Writer.prompt
