"""Research depth settings, parallel collection, the lesson curator, lookups and chat memory."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents import lesson_curator
from scholarmotion.agents.lesson_curator import (
    CoverageCheck,
    LessonCurator,
    LessonPlan,
    PlanReference,
    PlanSection,
    SourceNote,
    SourceNotes,
)
from scholarmotion.agents.reference_lookup import LookupRequest, look_up
from scholarmotion.agents.script_writer import LessonScript, ScriptReview, write_script
from scholarmotion.api.main import (
    _hash_password,
    _issue_session,
    research_options,
    update_authenticated_user,
)
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, TutorMessage, UsageEvent, UserAccount
from scholarmotion.retrieval import collection, source_ingestion
from scholarmotion.retrieval.source_ingestion import LinkUnreadable, ingest_links
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, SourceReference, UserAccountUpdate
from scholarmotion.services import chat_memory
from scholarmotion.services.research_settings import resolve


class Model:
    """Answers each schema from a queue and records every prompt."""

    name = "test"

    def __init__(self, answers: dict):
        self.answers = answers
        self.prompts: list[tuple[type, str]] = []

    async def generate_structured(self, prompt, schema, **_kwargs):
        self.prompts.append((schema, prompt))
        answer = self.answers[schema]
        return answer.pop(0) if isinstance(answer, list) else answer

    def asked(self, schema) -> list[str]:
        return [prompt for kind, prompt in self.prompts if kind is schema]


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_research_settings_default_and_are_clamped():
    defaults = resolve(None)
    assert (defaults.web_searches, defaults.library_top_k, defaults.chat_turns_kept) == (3, 20, 7)
    clamped = resolve({"web_searches": 500, "library_top_k": "2", "chat_turns_kept": "x"})
    assert (clamped.web_searches, clamped.library_top_k, clamped.chat_turns_kept) == (10, 5, 7)


@pytest.mark.asyncio
async def test_a_learner_saves_research_settings_and_sees_their_cost(sessions):
    async with sessions() as session:
        user = UserAccount(username="a", password_hash=_hash_password("pw123456"), display_name="A")
        session.add(user)
        await session.flush()
        token = f"Bearer {await _issue_session(session, user)}"
        session.add(
            UsageEvent(
                run_id="r",
                operation="build",
                stage="web_research",
                purpose="web_research:web_answer",
                model="m",
                cost_usd=0.05,
            )
        )
        await session.commit()
        saved = await update_authenticated_user(
            UserAccountUpdate(research_settings={"web_searches": 6, "web_pages_read": 99}),
            session,
            authorization=token,
        )
        assert saved["research_settings"]["web_searches"] == 6
        assert saved["research_settings"]["web_pages_read"] == 10
        options = {
            item["key"]: item for item in (await research_options(session, token))["settings"]
        }
        assert options["web_searches"]["cost_per_step_usd"] == pytest.approx(0.05)
        assert options["web_searches"]["default"] == 3


@pytest.mark.asyncio
async def test_links_download_in_parallel_and_the_first_unreadable_one_is_reported(
    monkeypatch, sessions
):
    running, peak = 0, 0

    async def read(url):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        if "bad" in url:
            raise LinkUnreadable(url, "the site could not be reached")
        return SimpleNamespace(url=url)

    async def index(_session, _settings, _store, _project_id, url, _fetched):
        return SimpleNamespace(title=url)

    monkeypatch.setattr(source_ingestion, "_read_link", read)
    monkeypatch.setattr(source_ingestion, "_index_fetched", index)
    async with sessions() as session:
        project = Project(title="t", request="r")
        session.add(project)
        await session.flush()
        urls = ["https://a.example/1", "https://a.example/2", "https://a.example/3"]
        documents = await ingest_links(session, None, None, project.id, urls)
        assert [document.title for document in documents] == urls
        assert peak == 3
        with pytest.raises(LinkUnreadable) as raised:
            await ingest_links(
                session, None, None, project.id, ["https://bad.example/1", "https://bad.example/2"]
            )
        assert raised.value.url == "https://bad.example/1"


@pytest.mark.asyncio
async def test_web_searches_run_in_parallel_and_read_the_top_pages(monkeypatch):
    searched = []

    async def search(_llm, query, **_kwargs):
        searched.append(query)
        await asyncio.sleep(0.01)
        return [{"id": "web-1", "text": f"fact about {query}", "url": f"https://{query}.example"}]

    async def page(url, index):
        return [{"id": f"webpage-{index}-1", "text": f"page {url}", "url": url}]

    monkeypatch.setattr(collection, "web_research", search)
    monkeypatch.setattr(collection, "_read_page", page)
    chunks = await collection.search_web(
        object(), ["mass", "energy", "light"], results_per_search=5, pages_to_read=2
    )
    assert sorted(searched) == ["energy", "light", "mass"]
    ids = [chunk["id"] for chunk in chunks]
    assert len(ids) == len(set(ids)) == 5  # three facts + two pages read in full


@pytest.mark.asyncio
async def test_queries_are_planned_only_when_more_than_one_is_allowed():
    model = Model({collection.SearchPlan: collection.SearchPlan(queries=["b", "a", "c", "d"])})
    assert await collection.plan_queries(model, "brief", "a", 1) == ["a"]
    assert await collection.plan_queries(model, "brief", "a", 3) == ["a", "b", "c"]
    assert len(model.prompts) == 1


def _chunks(count: int, size: int = 50) -> list[dict]:
    return [
        {"id": f"chunk-{index}", "document_title": "Blog", "text": f"passage {index} " + "x" * size}
        for index in range(1, count + 1)
    ]


def _plan(**extra) -> LessonPlan:
    return LessonPlan(
        title="Linga energy",
        ask_kind="walkthrough of a blog post",
        sections=[
            PlanSection(
                title="Fields",
                purpose="Fields and quanta",
                weight=1,
                key_points=[":", "Rudras are force fields"],
                examples=["E squared = p squared c squared + m squared c to the fourth"],
                quotes=["a mixture of quantum fields"],
                visual_ideas=["a vibrating field emitting particles"],
                sources=["P1", "P9"],
            ),
            PlanSection(title="Mass shell", purpose="The hyperboloid", weight=3, sources=["P2"]),
        ],
        references=[PlanReference(about="Derivation of the mass shell", sources=["P2"])],
        ending="sum up and invite questions",
        **extra,
    )


@pytest.mark.asyncio
async def test_material_that_fits_is_curated_in_one_pass_and_gaps_are_fixed():
    model = Model(
        {
            LessonPlan: [_plan(), _plan()],
            CoverageCheck: CoverageCheck(complete=False, missing=["the zero-point energy fact"]),
        }
    )
    plan = await LessonCurator(model).curate(
        request="Explain this blog",
        chunks=_chunks(2),
        minutes=2,
        level="general",
        source_scope="only_given",
        one_pass_chars=10_000,
    )
    assert plan.kind == "curated" and plan.ask_kind.startswith("walkthrough")
    # Passage aliases map back to chunk ids; an alias the material never had is dropped.
    assert plan.items[0].source_ids == ["chunk-1"]
    assert plan.references[0] == SourceReference(
        about="Derivation of the mass shell", source_ids=["chunk-2"]
    )
    assert plan.items[1].duration_seconds == pytest.approx(90)
    assert any(point.startswith("Example:") for point in plan.items[0].key_points)
    assert ":" not in plan.items[0].key_points  # separator items are dropped
    assert plan.items[0].visual_ideas == ["a vibrating field emitting particles"]
    revision = model.asked(LessonPlan)[1]
    assert "the zero-point energy fact" in revision and "YOUR DRAFT" in revision


@pytest.mark.asyncio
async def test_large_material_is_read_in_parallel_groups_then_merged(monkeypatch):
    helper = Model(
        {
            SourceNotes: SourceNotes(
                notes=[SourceNote(topic="fields", content="Rudras are fields", sources=["P1"])],
                references=[PlanReference(about="footnote", sources=["P5"])],
            ),
            CoverageCheck: CoverageCheck(complete=True),
        }
    )
    main = Model({LessonPlan: _plan()})
    plan = await LessonCurator(main, helper=helper).curate(
        request="Explain this blog",
        chunks=_chunks(6, size=400),
        minutes=2,
        level="general",
        source_scope="only_given",
        one_pass_chars=1_000,
    )
    groups = helper.asked(SourceNotes)
    assert len(groups) == 3  # two passages of about 420 characters fit in each group
    assert "[P1]" in groups[0] and "[P3]" in groups[1] and "[P1]" not in groups[1]
    merged = main.asked(LessonPlan)[0]
    assert "NOTES ON PART 3 OF 3" in merged and "(reference) footnote" in merged
    assert plan.kind == "curated"


@pytest.mark.asyncio
async def test_a_writer_looks_up_what_the_plan_left_out_within_its_limit():
    model = Model({LookupRequest: LookupRequest(references=["R1", "R7"], searches=["bosons"])})
    searched = []

    async def search(query):
        searched.append(query)
        return [{"document_title": "Blog", "text": "bosons carry force"}]

    found = await look_up(
        model,
        task="write the narration",
        plan="plan",
        references=[SourceReference(about="derivation", source_ids=["c2"])],
        chunks_by_id={"c2": {"document_title": "Blog", "text": "the derivation in full"}},
        search=search,
        max_lookups=2,
        stage="reference_lookup",
    )
    assert "the derivation in full" in found and "bosons carry force" in found
    assert searched == ["bosons"]
    assert (
        await look_up(
            model,
            task="t",
            plan="p",
            references=[],
            chunks_by_id={},
            search=search,
            max_lookups=0,
            stage="s",
        )
        == ""
    )


@pytest.mark.asyncio
async def test_the_script_writer_works_from_the_curated_plan_only():
    model = Model(
        {
            LessonScript: LessonScript(
                segments=[{"narration": "I will show you the fields.", "equations": []}]
            ),
            ScriptReview: ScriptReview(approved=True),
        }
    )
    asked = []

    async def lookup(task, plan):
        asked.append((task, plan))
        return "the derivation in full"

    curriculum = CurriculumPlan(
        topic="Linga",
        kind="curated",
        ending="sum up and invite questions",
        items=[
            CurriculumItem(
                title="Fields",
                objective="Fields and quanta",
                duration_seconds=30,
                key_points=["Rudras are force fields"],
            )
        ],
    )
    await write_script(
        model,
        curriculum,
        {"evidence": [{"kind": "raw", "content": "RAW MATERIAL"}]},
        learner_request="Explain this blog",
        lookup=lookup,
    )
    prompt = model.asked(LessonScript)[0]
    assert "LESSON PLAN" in prompt and "Rudras are force fields" in prompt
    assert "RAW MATERIAL" not in prompt
    assert "the derivation in full" in prompt and "sum up and invite questions" in prompt
    assert asked and "Rudras are force fields" in asked[0][1]


def _messages(project_id, turns: int, size: int = 400) -> list[TutorMessage]:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    messages = []
    for turn in range(turns):
        for offset, role in enumerate(("user", "assistant")):
            text = f"{role} turn {turn} " + "y" * size
            messages.append(
                TutorMessage(
                    project_id=project_id,
                    role=role,
                    canonical_text=text,
                    display_text=text,
                    created_at=start + timedelta(minutes=turn * 2 + offset),
                )
            )
    return messages


def test_recent_turns_stay_verbatim_and_older_ones_wait_for_compaction():
    project = Project(title="t", request="r")
    messages = _messages(project.id, 10)
    text, recent = chat_memory.conversation_context(project, messages, 7)
    assert recent[0].canonical_text.startswith("user turn 3")
    # Not yet summarised: older turns are passed on word for word, nothing is lost.
    assert "user turn 0" in text and "RECENT TURNS:" in text
    project.chat_summary = "Learner wants first-person narration."
    project.chat_summary_until = messages[3].created_at
    text, _ = chat_memory.conversation_context(project, messages, 7)
    assert "Learner wants first-person narration." in text
    assert "user turn 0" not in text and "user turn 2" in text


@pytest.mark.asyncio
async def test_long_chats_are_compacted_and_an_edit_clears_a_stale_summary(sessions):
    async with sessions() as session:
        project = Project(title="t", request="r")
        session.add(project)
        await session.flush()
        updated = project.updated_at
        messages = _messages(project.id, 12)
        session.add_all(messages)
        await session.flush()
        model = Model({chat_memory.ChatSummary: chat_memory.ChatSummary(summary="So far: waves.")})
        assert await chat_memory.compact(session, model, project, messages[:6], 7) is False
        assert await chat_memory.compact(session, model, project, messages, 7) is True
        await session.commit()
        await session.refresh(project)
        assert project.chat_summary == "So far: waves."
        # Memory upkeep does not reorder the chat list.
        assert chat_memory._aware(project.updated_at) == chat_memory._aware(updated)
        assert chat_memory._aware(project.chat_summary_until) == messages[9].created_at
        assert "assistant turn 4" in model.asked(chat_memory.ChatSummary)[0]
        assert chat_memory.forget_from(project, messages[20].created_at) is False
        assert chat_memory.forget_from(project, messages[2].created_at) is True
        assert project.chat_summary is None


@pytest.mark.asyncio
async def test_without_a_real_model_the_plain_outline_is_used():
    plan = await lesson_curator.LessonCurator(SimpleNamespace(name="mock")).curate(
        request="r", chunks=[], minutes=1, level="general", source_scope="none", one_pass_chars=10
    )
    assert plan is None


@pytest.mark.asyncio
async def test_library_search_uses_every_query_and_only_this_chat_and_the_library(sessions):
    from qdrant_client import models

    from scholarmotion.persistence.models import SourceChunk, SourceDocument
    from scholarmotion.persistence.qdrant import _any_of
    from scholarmotion.retrieval.qdrant_retrieval import QdrantHybridRetriever

    condition = _any_of(models, "project_id", ["p1", "None", None])
    assert condition.should[0].match.any == ["p1", "None"] and len(condition.should) == 3

    async with sessions() as session:
        project = Project(title="t", request="r")
        session.add(project)
        await session.flush()
        library = SourceDocument(project_id=None, kind="book", title="NCERT", uri="u")
        mine = SourceDocument(project_id=project.id, kind="web_page", title="Blog", uri="u")
        session.add_all([library, mine])
        await session.flush()
        first = SourceChunk(document_id=library.id, page=1, content_type="text", text="waves")
        second = SourceChunk(document_id=mine.id, page=1, content_type="text", text="light")
        session.add_all([first, second])
        await session.flush()

        class Store:
            def __init__(self):
                self.filters = []

            async def query_sources(self, vector, *, filters, limit):
                self.filters.append(filters)
                if vector == [1.0]:
                    return [(str(first.id), 0.4, {}), (str(second.id), 0.2, {})]
                return [(str(second.id), 0.9, {})]

        class Embeddings:
            async def embed(self, texts):
                return [[1.0] if text == "waves" else [2.0] for text in texts]

        store = Store()
        found = await QdrantHybridRetriever(Embeddings(), store).search_many(
            session, ["waves", "light", "waves"], project_id=str(project.id), limit=5
        )
        assert len(store.filters) == 2  # duplicate queries are searched once
        assert store.filters[0]["project_id"] == [str(project.id), "None", None]
        assert [(item.chunk["text"], item.score) for item in found] == [
            ("light", 0.9),
            ("waves", 0.4),
        ]


def test_wav_duration_accepts_a_path(tmp_path):
    import wave

    from scholarmotion.media.audio import wav_duration

    target = tmp_path / "a.wav"
    with wave.open(str(target), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000)
    assert wav_duration(target) == wav_duration(str(target)) == 1.0
