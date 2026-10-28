"""The knowledge map reads chats into a per-learner concept graph, and chats get model titles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from scholarmotion.agents.chat_title import ChatTitle, title_for_request
from scholarmotion.agents.concept_graph import (
    Concept,
    ConceptMap,
    GraphAnswer,
    Relation,
    extract_concepts,
)
from scholarmotion.api import knowledge
from scholarmotion.api import main as api_main
from scholarmotion.api.main import current_user_id
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import Project, TutorMessage, UserAccount
from scholarmotion.persistence.qdrant import QdrantUnavailable
from scholarmotion.services import embeddings


class _Model:
    name = "test"

    def __init__(self, answers):
        self.answers = answers
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **_kwargs):
        self.prompts.append(prompt)
        answer = self.answers[schema]
        return answer.pop(0) if isinstance(answer, list) else answer


class _Embedder:
    async def embed(self, texts):
        return [[1.0, 0.0] if "wave" in text.casefold() else [0.0, 1.0] for text in texts]


class _NoQdrant:
    """Qdrant is down: the graph must still work from the vectors kept in PostgreSQL."""

    async def upsert_knowledge_nodes(self, _points):
        raise QdrantUnavailable("down")

    async def delete_knowledge_nodes(self, _ids):
        raise QdrantUnavailable("down")

    async def query_knowledge(self, *_args, **_kwargs):
        raise QdrantUnavailable("down")


@pytest.fixture
async def world(tmp_path, monkeypatch):
    database = Database("sqlite+aiosqlite:///" + (tmp_path / "graph.db").as_posix())
    await database.create_all()
    model = _Model({})
    monkeypatch.setattr(knowledge, "create_llm_provider", lambda _settings: model)
    monkeypatch.setattr(embeddings, "create_embedding_provider", lambda _settings: _Embedder())
    monkeypatch.setattr(embeddings, "_PROVIDERS", {})

    async def session_dependency():
        async with database.sessions() as session:
            yield session

    app = FastAPI()

    @app.middleware("http")
    async def as_user(request: Request, call_next):
        user = request.headers.get("x-user")
        token = current_user_id.set(UUID(user) if user else None)
        try:
            return await call_next(request)
        finally:
            current_user_id.reset(token)

    app.include_router(
        knowledge.create_knowledge_router(
            Settings(_env_file=None), session_dependency, current_user_id, _NoQdrant()
        )
    )
    async with database.sessions() as session:
        learner = UserAccount(username="learner", password_hash="x", display_name="Asha")
        other = UserAccount(username="other", password_hash="x", display_name="Ravi")
        session.add_all([learner, other])
        await session.flush()
        chats = {}
        for minute, title in enumerate(("Waves", "Light")):
            chat = Project(owner_id=learner.id, title=title, request=title, original_request=title)
            session.add(chat)
            await session.flush()
            session.add(
                TutorMessage(
                    project_id=chat.id,
                    role="user",
                    canonical_text=f"Explain {title}",
                    display_text=f"Explain {title}",
                    created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minute),
                )
            )
            chats[title] = chat.id
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "model": model,
            "me": {"x-user": str(learner.id)},
            "other": {"x-user": str(other.id)},
            "chats": chats,
            "database": database,
        }
    await database.close()


def _maps():
    return [
        ConceptMap(
            concepts=[
                Concept(
                    name="Wavelength", description="Distance between wave crests", kind="Quantity"
                ),
                Concept(name="Frequency", description="Waves per second", kind="quantity"),
            ],
            relations=[
                Relation(
                    source="wavelength", target="Frequency", relation="is inversely related to"
                )
            ],
        ),
        ConceptMap(
            concepts=[
                Concept(name="wavelength", description="ignored, already described"),
                Concept(
                    name="Colour", description="How the eye sees wavelength", kind="perception"
                ),
            ],
            relations=[
                Relation(source="Colour", target="wavelength", relation="is set by"),
                Relation(source="Colour", target="Prism", relation="is split by"),
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_chats_become_one_graph_with_shared_concepts(world):
    client, model = world["client"], world["model"]
    model.answers[ConceptMap] = _maps()
    synced = (await client.post("/v1/knowledge/sync", headers=world["me"])).json()
    assert (synced["read"], synced["pending"]) == (2, 0)
    graph = (await client.get("/v1/knowledge/graph", headers=world["me"])).json()
    names = {node["name"]: node for node in graph["nodes"]}
    # "wavelength" from the second chat is the same node as "Wavelength" from the first.
    assert set(names) == {"Wavelength", "Frequency", "Colour"}
    assert names["Wavelength"]["mentions"] == 2
    assert names["Wavelength"]["kind"] == "quantity"
    # A relation to a concept the map does not define ("Prism") is dropped.
    assert {edge["relation"] for edge in graph["edges"]} == {"is inversely related to", "is set by"}
    # The second reading saw the first chat's concepts, so it could reuse their names.
    assert "Wavelength" in model.prompts[-1]
    assert (await client.get("/v1/knowledge/graph", headers=world["other"])).json()["nodes"] == []

    detail = (
        await client.get(f"/v1/knowledge/nodes/{names['Wavelength']['id']}", headers=world["me"])
    ).json()
    assert {item["relation"] for item in detail["relations"]} == {
        "is inversely related to",
        "is set by",
    }
    assert {chat["title"] for chat in detail["chats"]} == {"Waves", "Light"}
    hidden = await client.get(
        f"/v1/knowledge/nodes/{names['Wavelength']['id']}", headers=world["other"]
    )
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_questions_are_answered_from_the_closest_concepts(world):
    client, model = world["client"], world["model"]
    model.answers[ConceptMap] = _maps()
    await client.post("/v1/knowledge/sync", headers=world["me"])
    model.answers[GraphAnswer] = GraphAnswer(
        answer="Shorter wavelength means higher frequency.", concepts=["Wavelength", "Frequency"]
    )
    reply = (
        await client.post(
            "/v1/knowledge/query",
            json={"question": "how are waves and frequency linked?"},
            headers=world["me"],
        )
    ).json()
    assert reply["answer"].startswith("Shorter wavelength")
    assert "Wavelength is inversely related to Frequency" in model.prompts[-1]
    assert len(reply["nodes"]) >= 2
    assert {chat["title"] for chat in reply["chats"]} >= {"Waves"}


@pytest.mark.asyncio
async def test_only_chats_with_new_messages_are_read_again(world):
    client, model = world["client"], world["model"]
    model.answers[ConceptMap] = _maps()
    await client.post("/v1/knowledge/sync", headers=world["me"])
    again = (await client.post("/v1/knowledge/sync", headers=world["me"])).json()
    assert again == {"read": 0, "failed": 0, "pending": 0}
    async with world["database"].sessions() as session:
        session.add(
            TutorMessage(
                project_id=world["chats"]["Light"],
                role="user",
                canonical_text="And refraction?",
                display_text="And refraction?",
            )
        )
        await session.commit()
    model.answers[ConceptMap] = [
        ConceptMap(concepts=[Concept(name="Refraction", description="Bending of light")])
    ]
    later = (await client.post("/v1/knowledge/sync", headers=world["me"])).json()
    assert later["read"] == 1
    names = {
        node["name"]
        for node in (await client.get("/v1/knowledge/graph", headers=world["me"])).json()["nodes"]
    }
    # Newest first: Light was read with the first map, Waves with the second. Re-reading
    # Light drops Frequency (only Light had it); Wavelength stays because Waves has it.
    assert names == {"Wavelength", "Colour", "Refraction"}


@pytest.mark.asyncio
async def test_extraction_needs_a_real_model():
    class Mock:
        name = "mock"

    with pytest.raises(knowledge.GraphUnavailable):
        await extract_concepts(Mock(), title="t", conversation="c", lesson="", known=[])


@pytest.mark.asyncio
async def test_a_chat_title_is_written_by_the_model_in_the_learners_words():
    model = _Model({ChatTitle: ChatTitle(title='"ஒளிச்சேர்க்கை எப்படி வேலை செய்கிறது."')})
    title = await title_for_request(model, "ஒளிச்சேர்க்கை எப்படி வேலை செய்கிறது என்று விளக்கு")
    assert title == "ஒளிச்சேர்க்கை எப்படி வேலை செய்கிறது"


@pytest.mark.asyncio
async def test_a_renamed_chat_keeps_the_learners_title(tmp_path, monkeypatch):
    database = Database("sqlite+aiosqlite:///" + (tmp_path / "titles.db").as_posix())
    await database.create_all()
    monkeypatch.setattr(api_main, "database", database)
    monkeypatch.setattr(
        api_main,
        "create_llm_provider",
        lambda _settings: _Model({ChatTitle: ChatTitle(title="Newton's Third Law")}),
    )
    async with database.sessions() as session:
        fresh = Project(title="explain newtons 3rd law pls", request="r", original_request="r")
        renamed = Project(title="My physics notes", request="r", original_request="r")
        session.add_all([fresh, renamed])
        await session.commit()
    await api_main.name_chat(fresh.id, None, "explain newtons 3rd law pls")
    await api_main.name_chat(renamed.id, None, "explain something")
    async with database.sessions() as session:
        assert (await session.get(Project, fresh.id)).title == "Newton's Third Law"
        assert (await session.get(Project, renamed.id)).title == "My physics notes"
    await database.close()


@pytest.mark.asyncio
async def test_background_work_keeps_the_chat_order(world):
    """Reading chats into the map and model titles must not move old chats to the top."""
    async with world["database"].sessions() as session:
        before = {
            chat.id: chat.updated_at for chat in (await session.scalars(select(Project))).all()
        }
    world["model"].answers[ConceptMap] = _maps()
    await world["client"].post("/v1/knowledge/sync", headers=world["me"])
    async with world["database"].sessions() as session:
        chats = (await session.scalars(select(Project))).all()
        assert all(chat.graph_synced_at is not None for chat in chats)
        assert {chat.id: chat.updated_at for chat in chats} == before
