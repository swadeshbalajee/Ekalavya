"""A new lesson in a finished chat is a full new video version; the earlier one stays."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.api import service as service_api
from scholarmotion.api import tutor as tutor_api
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import (
    AudioArtifact,
    Chapter,
    GeneratedCode,
    Project,
    ProjectArtifact,
    RenderArtifact,
    Scene,
    SceneSpecification,
    TimelineSegment,
)
from scholarmotion.schemas import TutorChatRequest


class _Echo:
    def __init__(self, *_args):
        pass

    async def translate(self, text, **_kwargs):
        return text

    async def to_english(self, text, **_kwargs):
        return text


class _Planner:
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


async def _english_video(session) -> Project:
    """A chat with a finished 2-scene English video (v1, scene artifacts up to v3)."""
    project = Project(
        title="Blog",
        request="Explain the blog",
        original_request="Explain the blog",
        mode="both",
        status="COMPLETE",
        text_language="English",
        voice_language="English",
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_key="C01", title="Blog", order_index=1)
    session.add(chapter)
    await session.flush()
    session.add(
        ProjectArtifact(
            project_id=project.id,
            kind="video",
            version=1,
            uri="v1/final.mp4",
            payload={"text_language": "English", "voice_language": "English"},
        )
    )
    for index, (start, end) in enumerate([(0, 24), (24, 61)], 1):
        scene = Scene(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_key=f"S{index:02d}",
            title=f"Scene {index}",
            order_index=index,
            timeline_start=start,
            timeline_end=end,
        )
        session.add(scene)
        await session.flush()
        session.add_all(
            [
                SceneSpecification(scene_id=scene.id, version=1, payload={}, uri="s"),
                GeneratedCode(
                    scene_id=scene.id, version=1, spec_version=1, uri="c", scene_class="S"
                ),
                AudioArtifact(
                    scene_id=scene.id, version=1, narration_version=1, uri="a", duration=1
                ),
                RenderArtifact(
                    scene_id=scene.id, version=3, code_version=1, audio_version=1, uri="r"
                ),
                TimelineSegment(
                    project_id=project.id,
                    video_version=1,
                    scene_id=scene.id,
                    start_seconds=start,
                    end_seconds=end,
                    render_version=3,
                ),
            ]
        )
    await session.flush()
    return project


@pytest.mark.asyncio
async def test_the_next_lesson_is_numbered_above_every_existing_version(session):
    project = await _english_video(session)
    assert await service_api.next_build_versions(session, project.id) == (2, 3)


@pytest.mark.asyncio
async def test_a_new_lesson_in_a_finished_chat_is_a_full_build_in_the_asked_language(
    monkeypatch, session
):
    project = await _english_video(session)
    monkeypatch.setattr(tutor_api, "TranslationService", _Echo)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(
        tutor_api,
        "create_llm_provider",
        lambda _settings: _Planner(
            action="generate_video",
            text_language="Tamil",
            voice_language="Tamil",
            duration_minutes=3,
            canonical_request="Explain the blog in detail with its examples.",
        ),
    )
    decision = await tutor_api.respond(
        session,
        Settings(),
        project,
        TutorChatRequest(message="make this video in tamil, in detail"),
    )
    # Not an edit of the old video's range: a new build with the planner's settings.
    assert decision.action == "generate_video"
    assert (project.text_language, project.voice_language) == ("Tamil", "Tamil")
    assert project.target_duration_minutes == 3
    assert "earlier video stays available" in decision.canonical_message


@pytest.mark.asyncio
async def test_restoring_a_version_leaves_out_scenes_it_did_not_have(session):
    project = await _english_video(session)
    # A later lesson (v2) used only S01 plus a new S03.
    newer = Scene(
        project_id=project.id,
        chapter_id=(
            await session.get(Scene, (await tutor_api.video_scenes(session, project))[0].id)
        ).chapter_id,
        scene_key="S03",
        title="New",
        order_index=3,
        timeline_start=40,
        timeline_end=80,
    )
    session.add(newer)
    await session.flush()
    session.add(
        RenderArtifact(scene_id=newer.id, version=4, code_version=4, audio_version=4, uri="r")
    )
    await session.flush()
    assert await service_api.activate_video_version(session, project, 1)
    current = await tutor_api.video_scenes(session, project)
    assert [scene.scene_key for scene in current] == ["S01", "S02"]
    assert (newer.timeline_start, newer.timeline_end) == (None, None)
    assert project.text_language == "English"
