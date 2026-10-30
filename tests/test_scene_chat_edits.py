"""Scene requests in chat follow the planner's decision; every video version stays playable."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api import main as api_main
from scholarmotion.api import tutor as tutor_api
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, Scene
from scholarmotion.schemas import TutorChatRequest
from scholarmotion.services.edit_plan import EditPlan


class _Echo:
    def __init__(self, *_args):
        pass

    async def translate(self, text, **_kwargs):
        return text

    async def to_english(self, text, **_kwargs):
        return text


class _Planner:
    """Stands in for the planner model: returns a fixed decision for the turn."""

    name = "test"

    def __init__(self, **decision):
        self.decision = decision

    async def generate_structured(self, _prompt, schema, *, temperature=None):
        return schema(**{"canonical_message": "turn", **self.decision})


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as value:
        yield value
    await engine.dispose()


async def _finished_project(session) -> Project:
    project = Project(
        title="Newton",
        request="Explain Newton's third law",
        original_request="Explain Newton's third law",
        mode="both",
        status="COMPLETE",
        text_language="English",
        voice_language="English",
    )
    session.add(project)
    await session.flush()
    for index, (start, end) in enumerate([(0, 30), (30, 61), (61, 91)], 1):
        session.add(
            Scene(
                project_id=project.id,
                scene_key=f"S{index:02d}",
                title=f"Scene {index}",
                order_index=index,
                timeline_start=start,
                timeline_end=end,
            )
        )
    await session.flush()
    return project


async def _respond(monkeypatch, session, project, **decision):
    monkeypatch.setattr(tutor_api, "TranslationService", _Echo)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: _Planner(**decision))
    return await tutor_api.respond(
        session, Settings(), project, TutorChatRequest(message="(learner turn)")
    )


@pytest.mark.asyncio
async def test_a_one_scene_language_change_localizes_only_that_scene(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="localize_video",
        target_scenes=["S02"],
        text_language="Tamil",
        voice_language="Tamil",
    )
    assert (decision.action, decision.instruction) == ("localize_scene", "S02")
    assert (decision.start, decision.end) == (30, 61)


@pytest.mark.asyncio
async def test_a_scene_edit_rebuilds_that_scenes_time_range(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="edit_video",
        target_scenes=["S03"],
        edit_instruction="Use one simple example.",
    )
    assert decision.action == "edit_range"
    assert (decision.start, decision.end) == (61, 91)
    edits = EditPlan.decode(decision.instruction, []).edits
    assert [edit.instruction for edit in edits] == ["Use one simple example."]


@pytest.mark.asyncio
async def test_an_edit_of_the_whole_video_rebuilds_every_scene(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(monkeypatch, session, project, action="edit_video", whole_video=True)
    assert decision.action == "edit_range"
    assert (decision.start, decision.end) == (0, 91)


@pytest.mark.asyncio
async def test_an_edit_with_no_part_named_applies_to_the_whole_video(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(monkeypatch, session, project, action="edit_video")
    assert decision.action == "edit_range"
    assert (decision.start, decision.end) == (0, 91)


@pytest.mark.asyncio
async def test_the_planners_own_words_are_the_reply(monkeypatch, session):
    project = await _finished_project(session)
    asked = await _respond(
        monkeypatch, session, project, action="answer", reply="Do you mean the torque scene?"
    )
    assert (asked.action, asked.canonical_message) == ("answer", "Do you mean the torque scene?")
    confirmed = await _respond(
        monkeypatch,
        session,
        project,
        action="edit_video",
        target_scenes=["S01"],
        reply="I'll slow down the opening scene.",
    )
    assert confirmed.canonical_message == "I'll slow down the opening scene."


@pytest.mark.asyncio
async def test_several_scenes_can_change_language_together(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="localize_video",
        target_scenes=["S01", "S03"],
        text_language="Tamil",
        voice_language="Tamil",
    )
    assert (decision.action, decision.instruction) == ("localize_scene", "S01,S03")


@pytest.mark.asyncio
async def test_a_time_range_outside_the_video_is_rejected(monkeypatch, session):
    project = await _finished_project(session)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="edit_video",
        edit_start_seconds=200,
        edit_end_seconds=260,
    )
    assert decision.action == "answer"


def test_earlier_video_versions_are_still_served(tmp_path: Path):
    for version in (1, 2):
        folder = tmp_path / "video" / f"v{version}"
        folder.mkdir(parents=True)
        (folder / "final.mp4").write_bytes(b"mp4")
    project = SimpleNamespace(active_video_path=str(tmp_path / "video" / "v2" / "final.mp4"))
    assert api_main._video_file(project, 1, "final.mp4") == tmp_path / "video" / "v1" / "final.mp4"
    assert api_main._video_file(project, None, "final.mp4").parent.name == "v2"
    assert api_main._video_file(project, 7, "final.mp4") is None


@pytest.mark.asyncio
async def test_an_edit_of_an_earlier_video_targets_that_version(monkeypatch, session):
    from scholarmotion.persistence.models import ProjectArtifact, TimelineSegment

    project = await _finished_project(session)
    scenes = await tutor_api.video_scenes(session, project)
    for version, shift in ((1, 0), (2, 5)):
        session.add(
            ProjectArtifact(project_id=project.id, kind="video", version=version, uri=f"v{version}")
        )
        for scene in scenes:
            session.add(
                TimelineSegment(
                    project_id=project.id,
                    video_version=version,
                    scene_id=scene.id,
                    start_seconds=scene.timeline_start + (shift if scene.scene_key != "S01" else 0),
                    end_seconds=scene.timeline_end + shift,
                    render_version=version,
                )
            )
    await session.flush()
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="edit_video",
        target_scenes=["S02"],
        target_video_version=1,
        edit_instruction="Simpler.",
    )
    # The range comes from version 1's own timeline, and that version is restored first.
    assert (decision.action, decision.base_version) == ("edit_range", 1)
    assert (decision.start, decision.end) == (30, 61)


@pytest.mark.asyncio
async def test_the_current_video_is_never_a_restore_target(monkeypatch, session):
    from scholarmotion.persistence.models import ProjectArtifact

    project = await _finished_project(session)
    session.add(ProjectArtifact(project_id=project.id, kind="video", version=1, uri="v1"))
    await session.flush()
    decision = await _respond(
        monkeypatch,
        session,
        project,
        action="edit_video",
        target_scenes=["S02"],
        target_video_version=1,
    )
    assert decision.base_version is None
