from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.memory.correction_memory import CorrectionMemory
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers.llm.mock import MockLLMProvider
from scholarmotion.providers.tts.mock import MockTTSProvider
from scholarmotion.services import ScholarMotionPipeline
from scholarmotion.skills import SkillService
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge


@pytest.mark.asyncio
async def test_mock_pipeline_builds_versioned_video_artifacts(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await bootstrap_system_knowledge(session)
        await session.commit()
        progress_events = []

        async def track_progress(event, scene_id):
            progress_events.append((event, scene_id))

        pipeline = ScholarMotionPipeline(
            llm=MockLLMProvider(),
            code_llm=MockLLMProvider(),
            tts=MockTTSProvider(),
            embeddings=type(
                "Embeddings",
                (),
                {
                    "embed": lambda _, values: __import__("asyncio").sleep(
                        0, result=[[1.0] for _ in values]
                    )
                },
            )(),
            storage=LocalObjectStore(tmp_path),
            renderer=SandboxRenderer(manim_binary="__missing_manim__", mock_missing=True),
            assembler=FFmpegAssembler("__missing_ffmpeg__", mock_missing=True),
            correction_memory=CorrectionMemory(session),
            skill_service=SkillService(session),
            progress_callback=track_progress,
        )
        result = await pipeline.build(
            "project-e2e", "Explain magnetic flux for a Class 12 learner."
        )
        assert result.scenes
        assert result.timeline.duration_seconds > 0
        assert (tmp_path / "project-e2e/video/v1/final.mp4").exists()
        assert any(event == "scenes.created" for event, _ in progress_events)
        assert any(event == "scene.started" and scene_id for event, scene_id in progress_events)
        assert any(event == "video.assembling" for event, _ in progress_events)
        assert any(event == "video.complete" for event, _ in progress_events)
        retried = await pipeline.build(
            "project-e2e", "Explain magnetic flux for a Class 12 learner."
        )
        assert retried.scenes
        assert (tmp_path / "project-e2e/video/v1/final.mp4").exists()
    await engine.dispose()
