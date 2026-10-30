"""Usage dashboard: per-call records, per-user scoping, admin view and live pipeline state."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api.usage import create_usage_router, pipeline_state
from scholarmotion.config import Settings
from scholarmotion.observability.usage import (
    record_failure,
    record_service_usage,
    set_usage_scene,
    track_usage,
    usage_stage,
)
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import ProgressEvent, Project, UserAccount
from scholarmotion.services.usage_records import module_for, usage_rows

START = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def _event(name: str, seconds: float, scene: str | None = None, **detail) -> ProgressEvent:
    return ProgressEvent(
        event=name,
        created_at=START + timedelta(seconds=seconds),
        detail={**detail, **({"scene_key": scene} if scene else {})},
    )


@pytest.mark.parametrize(
    ("purpose", "module"),
    [
        ("script:structured", "Script"),
        ("storyboard:structured", "Storyboard"),
        ("code", "Manim code"),
        ("code_repair", "Code repair"),
        ("translation:translation", "Translation"),
        ("narration:tts", "Narration (TTS)"),
        ("video_review", "Video review"),
        ("lesson_review:structured", "Lesson review"),
    ],
)
def test_calls_are_grouped_into_dashboard_modules(purpose, module):
    assert module_for(purpose) == module


def test_ledger_entries_become_rows_with_scene_timing_and_outcome():
    with track_usage() as ledger:
        with usage_stage("narration"):
            record_service_usage(
                purpose="tts",
                model="bodhan/indic-speak",
                cost_usd=0.01,
                characters=900,
                provider="bodhan",
                latency_ms=1200,
            )
        set_usage_scene("S02")
        record_failure(
            purpose="code", model="anthropic/claude", provider="openrouter", latency_ms=90
        )
    rows = usage_rows(ledger, user_id=None, project_id=None, operation="build", run_id="r1")
    assert [(row.stage, row.scene_key, row.status) for row in rows] == [
        ("narration", None, "ok"),
        (None, "S02", "error"),
    ]
    assert rows[0].characters == 900 and rows[0].latency_ms == 1200


def test_a_running_operation_saves_only_calls_not_yet_saved():
    with track_usage() as ledger:
        record_service_usage(purpose="tts", model="m", cost_usd=0.01)
        first = ledger.unsaved()
        ledger.mark_saved(len(first))
        record_service_usage(purpose="translation", model="m", cost_usd=0.02)
        record_service_usage(purpose="tts", model="m", cost_usd=0.03)
        second = ledger.unsaved()
    assert [entry.cost_usd for entry in first] == [0.01]
    assert [entry.cost_usd for entry in second] == [0.02, 0.03]
    rows = usage_rows(
        ledger, user_id=None, project_id=None, operation="edit", run_id="r1", entries=second
    )
    assert [row.purpose for row in rows] == ["translation", "tts"]
    assert {row.run_id for row in rows} == {"r1"}


def test_a_running_build_shows_done_running_and_queued_stages():
    run = [
        _event("project.started", 0),
        _event("preparation.complete", 5),
        _event("retrieval.complete", 8),
        _event("curriculum.complete", 9),
        _event("script.complete", 40),
        _event("storyboard.complete", 50),
        _event("scenes.created", 51, scenes=["S01: Intro", "S02: Law", "S03: Example"]),
        _event("scene.started", 70, "S01"),
        _event("scene.complete", 120, "S01"),
        _event("scene.started", 71, "S02"),
        _event("scene.render_started", 100, "S02"),
        _event("scene.rejected", 110, "S02"),
        _event("scene.repairing", 111, "S02"),
    ]
    state = pipeline_state(run, active=True, failed=False)
    stages = {stage["key"]: stage["state"] for stage in state["stages"]}
    assert stages["script"] == "done" and stages["narration"] == "done"
    assert stages["scenes"] == "running"
    assert stages["review"] == "queued" and stages["assembly"] == "queued"
    lanes = {lane["scene"]: lane for lane in state["scenes"]}
    assert lanes["S01"]["state"] == "done"
    assert lanes["S02"]["attempt"] == 2
    assert [s["state"] for s in lanes["S02"]["steps"]][:3] == ["done", "done", "running"]
    assert lanes["S03"]["state"] == "queued"
    assert "S02 render (attempt 2)" in state["next"] and "1 more scene" in state["next"]


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_var: ContextVar[UUID | None] = ContextVar("user", default=None)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(
        create_usage_router(Settings(admin_usernames="boss"), session_dependency, user_var)
    )

    @app.middleware("http")
    async def as_user(request, call_next):
        token = user_var.set(UUID(request.headers["x-user"]))
        try:
            return await call_next(request)
        finally:
            user_var.reset(token)

    async with sessions() as session:
        learner = UserAccount(username="learner", password_hash="x", display_name="Learner")
        other = UserAccount(username="other", password_hash="x", display_name="Other")
        boss = UserAccount(username="boss", password_hash="x", display_name="Boss")
        session.add_all([learner, other, boss])
        await session.flush()
        mine = Project(
            owner_id=learner.id, title="Photoelectric effect", request="r", status="COMPLETE"
        )
        theirs = Project(owner_id=other.id, title="Their chat", request="r", status="GENERATING")
        session.add_all([mine, theirs])
        await session.flush()
        now = datetime.now(UTC)
        for offset, name in [
            (0, "project.started"),
            (300, "scene.rejected"),
            (600, "video.complete"),
        ]:
            session.add(
                ProgressEvent(
                    project_id=mine.id,
                    event=name,
                    detail={},
                    created_at=now - timedelta(hours=1) + timedelta(seconds=offset),
                )
            )
        session.add(
            ProgressEvent(project_id=theirs.id, event="project.started", detail={}, created_at=now)
        )
        with track_usage() as ledger:
            record_service_usage(purpose="code", model="m", cost_usd=0.10, input_tokens=1000)
            record_failure(purpose="code", model="m", provider="openrouter", latency_ms=50)
        session.add_all(
            usage_rows(
                ledger, user_id=learner.id, project_id=mine.id, operation="build", run_id="a"
            )
        )
        with track_usage() as ledger:
            record_service_usage(purpose="script:structured", model="m", cost_usd=0.30)
        session.add_all(
            usage_rows(
                ledger, user_id=other.id, project_id=theirs.id, operation="build", run_id="b"
            )
        )
        await session.commit()
        ids = {"learner": str(learner.id), "boss": str(boss.id), "mine": str(mine.id)}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, ids
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_user_sees_only_their_own_chats_spend_time_and_retries(client):
    http, ids = client
    body = (await http.get("/v1/usage/summary", headers={"x-user": ids["learner"]})).json()
    assert body["is_admin"] is False
    assert [chat["title"] for chat in body["chats"]] == ["Photoelectric effect"]
    chat = body["chats"][0]
    assert chat["cost_usd"] == 0.1 and chat["videos"] == 1
    assert chat["time_seconds"] == 600
    # One repair after a rejected render plus one failed provider attempt.
    assert chat["retries"] == 2
    assert body["totals"]["success_rate"] == 1.0
    assert body["modules"][0]["module"] == "Manim code"


@pytest.mark.asyncio
async def test_only_admins_can_see_all_users(client):
    http, ids = client
    denied = await http.get("/v1/usage/summary?scope=all", headers={"x-user": ids["learner"]})
    assert denied.status_code == 403
    body = (await http.get("/v1/usage/summary?scope=all", headers={"x-user": ids["boss"]})).json()
    assert {chat["title"] for chat in body["chats"]} == {"Photoelectric effect", "Their chat"}
    assert [user["user"] for user in body["users"]] == ["Other", "Learner"]
    live = (await http.get("/v1/usage/live?scope=all", headers={"x-user": ids["boss"]})).json()
    assert [run["title"] for run in live["runs"]] == ["Their chat"]


@pytest.mark.asyncio
async def test_chat_detail_has_modules_runs_and_calls(client):
    http, ids = client
    detail = (
        await http.get(f"/v1/usage/chats/{ids['mine']}", headers={"x-user": ids["learner"]})
    ).json()
    assert detail["runs"][0]["outcome"] == "complete"
    assert detail["runs"][0]["seconds"] == 600
    assert [call["status"] for call in detail["calls"]] == ["ok", "error"]
    other = await http.get(f"/v1/usage/chats/{ids['mine']}", headers={"x-user": ids["boss"]})
    assert other.status_code == 200
