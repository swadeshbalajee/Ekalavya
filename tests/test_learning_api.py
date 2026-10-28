"""Catalog, library, study plans and quizzes: who can see what, and what the agents decide."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.quiz_writer import GradedAnswer, GradedAnswers, QuizDraft, QuizQuestion
from scholarmotion.agents.study_planner import PlannedTask, StudyPlanDraft
from scholarmotion.agents.video_catalog import VideoCatalogEntry
from scholarmotion.api import learning
from scholarmotion.api.main import current_user_id
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, Scene, UserAccount
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.services import embeddings

VTT = """WEBVTT

1
00:00:00.000 --> 00:00:04.000
Light is a wave.

2
00:00:31.000 --> 00:00:35.000
Wavelength is the distance between crests.
"""


class _Model:
    """The planner model: answers each schema with the decision a test gives it."""

    name = "test"

    def __init__(self, answers: dict):
        self.answers = answers
        self.prompts: list[str] = []

    async def generate_structured(self, prompt, schema, **_kwargs):
        self.prompts.append(prompt)
        return self.answers[schema]


class _Embedder:
    """Two-word topics: optics videos point one way, everything else another."""

    async def embed(self, texts):
        return [[1.0, 0.0] if "light" in text.casefold() else [0.0, 1.0] for text in texts]


@pytest.fixture
async def world(tmp_path: Path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    model = _Model({})
    monkeypatch.setattr(learning, "create_llm_provider", lambda _settings: model)
    monkeypatch.setattr(embeddings, "create_embedding_provider", lambda _settings: _Embedder())
    monkeypatch.setattr(embeddings, "_PROVIDERS", {})

    async def session_dependency():
        async with sessions() as session:
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
        learning.create_learning_router(
            Settings(_env_file=None),
            LocalObjectStore(tmp_path),
            session_dependency,
            current_user_id,
        )
    )
    async with sessions() as session:
        owner = UserAccount(username="owner", password_hash="x", display_name="Asha")
        other = UserAccount(username="other", password_hash="x", display_name="Ravi")
        session.add_all([owner, other])
        await session.flush()
        videos = {}
        for name, title in (("light", "Light waves"), ("forces", "Newton's forces")):
            folder = tmp_path / name / "video" / "v1"
            folder.mkdir(parents=True)
            (folder / "final.mp4").write_bytes(b"mp4")
            (folder / "subtitles.vtt").write_text(VTT, encoding="utf-8")
            project = Project(
                owner_id=owner.id,
                title=title,
                request=f"Explain {title}",
                original_request=f"Explain {title}",
                status="COMPLETE",
                active_video_path=str(folder / "final.mp4"),
            )
            session.add(project)
            await session.flush()
            session.add_all(
                [
                    Scene(
                        project_id=project.id,
                        scene_key="S01",
                        title="What a wave is",
                        order_index=1,
                        timeline_start=0,
                        timeline_end=30,
                    ),
                    Scene(
                        project_id=project.id,
                        scene_key="S02",
                        title="Wavelength",
                        order_index=2,
                        timeline_start=30,
                        timeline_end=61,
                    ),
                ]
            )
            videos[name] = project.id
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "model": model,
            "owner": {"x-user": str(owner.id)},
            "other": {"x-user": str(other.id)},
            "videos": videos,
        }
    await engine.dispose()


async def _publish(world, name: str, **details):
    body = {"visibility": "public", "description": "A lesson.", **details}
    response = await world["client"].patch(
        f"/v1/videos/{world['videos'][name]}/catalog", json=body, headers=world["owner"]
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_vtt_cues_are_read_with_their_times():
    cues = learning.parse_vtt(VTT)
    assert [(cue["start"], cue["text"]) for cue in cues] == [
        (0.0, "Light is a wave."),
        (31.0, "Wavelength is the distance between crests."),
    ]


@pytest.mark.asyncio
async def test_a_video_is_private_until_its_owner_publishes_it(world):
    client, light = world["client"], world["videos"]["light"]
    assert (await client.get(f"/v1/videos/{light}", headers=world["other"])).status_code == 404
    assert (await client.get(f"/v1/videos/{light}")).status_code == 404
    assert (await client.get(f"/v1/videos/{light}", headers=world["owner"])).status_code == 200

    card = await _publish(world, "light", subject="Physics", tags=["optics"])
    assert (card["visibility"], card["author"]) == ("public", "Asha")
    anonymous = await client.get(f"/v1/videos/{light}")
    assert anonymous.status_code == 200
    assert [chapter["title"] for chapter in anonymous.json()["chapters"]] == [
        "What a wave is",
        "Wavelength",
    ]
    assert (await client.get(f"/v1/videos/{light}/video")).status_code == 200
    # Another learner cannot change someone else's video.
    denied = await client.patch(
        f"/v1/videos/{light}/catalog", json={"visibility": "private"}, headers=world["other"]
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_publishing_without_details_lets_the_catalog_agent_write_them(world):
    world["model"].answers[VideoCatalogEntry] = VideoCatalogEntry(
        title="How light travels as a wave",
        description="Crests, troughs and wavelength.",
        subject="Physics",
        tags=["light", "wavelength", "light"],
    )
    response = await world["client"].patch(
        f"/v1/videos/{world['videos']['light']}/catalog",
        json={"visibility": "public"},
        headers=world["owner"],
    )
    card = response.json()
    assert card["title"] == "How light travels as a wave"
    assert (card["subject"], card["tags"]) == ("Physics", ["light", "wavelength"])
    # The agent read the narration from the subtitles.
    assert "distance between crests" in world["model"].prompts[0]


@pytest.mark.asyncio
async def test_search_needs_every_keyword_and_falls_back_to_meaning(world):
    client = world["client"]
    await _publish(world, "light", subject="Physics", tags=["optics"])
    await _publish(world, "forces", subject="Physics", tags=["mechanics"])

    both = await client.get("/v1/videos", params={"q": "wavelength"})
    assert both.json()["total"] == 2  # a scene title of both videos
    one = await client.get("/v1/videos", params={"q": "wavelength optics"})
    assert [item["title"] for item in one.json()["items"]] == ["Light waves"]
    # No video has these words; the closest in meaning is still offered.
    related = (await client.get("/v1/videos", params={"q": "light colours"})).json()
    assert related["match"] == "related"
    assert [item["title"] for item in related["items"]] == ["Light waves"]
    subjects = (await client.get("/v1/videos/subjects")).json()
    assert subjects == [{"subject": "Physics", "count": 2}]


@pytest.mark.asyncio
async def test_progress_notes_saves_and_playlists_belong_to_the_viewer(world):
    client, other = world["client"], world["other"]
    await _publish(world, "light")
    light = world["videos"]["light"]

    await client.put(
        f"/v1/videos/{light}/progress",
        json={"position_seconds": 20, "duration_seconds": 61},
        headers=other,
    )
    home = (await client.get("/v1/home", headers=other)).json()
    assert [item["id"] for item in home["continue"]] == [str(light)]
    assert home["continue"][0]["progress"]["position"] == 20

    note = await client.post(
        f"/v1/videos/{light}/notes", json={"at_seconds": 31, "text": "crests!"}, headers=other
    )
    assert note.status_code == 201
    assert (await client.get(f"/v1/videos/{light}/notes", headers=world["owner"])).json() == []
    assert len((await client.get("/v1/library/notes", headers=other)).json()) == 1

    await client.put(f"/v1/videos/{light}/save", headers=other)
    library = (await client.get("/v1/library/videos", headers=other)).json()
    assert [item["id"] for item in library] == [str(light)]
    assert library[0]["saved"] is True

    playlist = (
        await client.post("/v1/library/playlists", json={"name": "Optics"}, headers=other)
    ).json()
    added = await client.post(
        f"/v1/library/playlists/{playlist['id']}/items",
        json={"video_id": str(light)},
        headers=other,
    )
    assert added.json()["count"] == 1
    # A private video cannot be added to someone else's playlist.
    private = await client.post(
        f"/v1/library/playlists/{playlist['id']}/items",
        json={"video_id": str(world["videos"]["forces"])},
        headers=other,
    )
    assert private.status_code == 404

    await client.put(
        f"/v1/videos/{light}/progress",
        json={"position_seconds": 60, "duration_seconds": 61},
        headers=other,
    )
    assert (await client.get("/v1/home", headers=other)).json()["continue"] == []


@pytest.mark.asyncio
async def test_the_study_planner_only_links_videos_the_learner_can_open(world):
    client = world["client"]
    light = world["videos"]["light"]
    world["model"].answers[StudyPlanDraft] = StudyPlanDraft(
        title="Optics in two weeks",
        tasks=[
            PlannedTask(day=0, kind="watch", title="Light waves", video_id=str(light)),
            PlannedTask(day=1, kind="watch", title="Lenses", video_id="not-a-real-video"),
            PlannedTask(day=2, kind="practice", title="Quiz on waves", video_id=str(light)),
        ],
    )
    start = date.today()
    created = await client.post(
        "/v1/study-plans",
        json={"goal": "Learn optics", "start_date": start.isoformat(), "minutes_per_day": 20},
        headers=world["owner"],
    )
    assert created.status_code == 201, created.text
    tasks = created.json()["tasks"]
    assert [(task["kind"], task["video_id"]) for task in tasks] == [
        ("watch", str(light)),
        ("create", None),
        ("practice", str(light)),
    ]
    assert tasks[1]["topic"] == "Lenses"
    assert "Light waves" in world["model"].prompts[-1]

    done = await client.patch(
        f"/v1/study-plans/tasks/{tasks[0]['id']}", json={"done": True}, headers=world["owner"]
    )
    assert done.json()["done"] is True
    window = await client.get(
        "/v1/study-plans/tasks",
        params={"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
        headers=world["owner"],
    )
    assert [task["title"] for task in window.json()] == ["Light waves", "Lenses"]
    assert (await client.get("/v1/study-plans", headers=world["other"])).json() == []


@pytest.mark.asyncio
async def test_a_quiz_hides_answers_until_graded_and_points_back_to_the_scene(world):
    client = world["client"]
    light = world["videos"]["light"]
    world["model"].answers[QuizDraft] = QuizDraft(
        title="Waves",
        questions=[
            QuizQuestion(
                kind="choice",
                prompt="What is wavelength?",
                options=["Crest to crest", "Wave height"],
                answer_index=0,
                scene_key="S02",
            ),
            QuizQuestion(kind="short", prompt="Is light a wave?", answer="Yes", scene_key="S01"),
            QuizQuestion(kind="choice", prompt="Broken", options=["only one"], answer_index=0),
        ],
    )
    world["model"].answers[GradedAnswers] = GradedAnswers(
        results=[GradedAnswer(index=1, correct=False, feedback="Say why.")]
    )
    quiz = (
        await client.post("/v1/quizzes", json={"video_id": str(light)}, headers=world["owner"])
    ).json()
    assert len(quiz["questions"]) == 2  # the unusable question was dropped
    assert "answer_index" not in quiz["questions"][0]
    assert "Wavelength is the distance between crests." in world["model"].prompts[-1]

    graded = (
        await client.post(
            f"/v1/quizzes/{quiz['id']}/attempts",
            json={"answers": [0, "it is a particle"]},
            headers=world["owner"],
        )
    ).json()
    assert graded["score"] == 0.5
    assert graded["results"][1]["feedback"] == "Say why."
    assert graded["results"][1]["at_seconds"] == 0
    assert graded["results"][0]["at_seconds"] == 30
    again = (await client.get(f"/v1/quizzes/{quiz['id']}", headers=world["owner"])).json()
    assert (again["attempts"], again["best_score"]) == (1, 0.5)


@pytest.mark.asyncio
async def test_recommendations_follow_what_the_learner_studies(world):
    client = world["client"]
    await _publish(world, "light")
    await _publish(world, "forces")
    # The other learner's study goal is about light.
    world["model"].answers[StudyPlanDraft] = StudyPlanDraft(title="Light", tasks=[])
    await client.post(
        "/v1/study-plans",
        json={"goal": "Understand light and colour", "minutes_per_day": 20},
        headers=world["other"],
    )
    recommended = (await client.get("/v1/videos/recommended", headers=world["other"])).json()
    assert recommended[0]["title"] == "Light waves"
    anonymous = (await client.get("/v1/videos/recommended")).json()
    assert {item["title"] for item in anonymous} == {"Light waves", "Newton's forces"}
