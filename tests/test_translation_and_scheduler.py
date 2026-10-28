from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.scene_compiler import compile_scene_specs
from scholarmotion.agents.script_writer import (
    _build_prompt,
    write_script,
)
from scholarmotion.api.tutor import chat_history, respond
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, TutorMessage
from scholarmotion.schemas import (
    CurriculumItem,
    CurriculumPlan,
    SceneSpec,
    TutorChatRequest,
    VisualBeat,
)
from scholarmotion.services.orchestration import ScholarMotionPipeline
from scholarmotion.translation.service import (
    TranslationService,
)


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        main_llm_provider="mock",
        translation_provider="mock",
    )


def _scene(scene_id: str) -> SceneSpec:
    return SceneSpec(
        scene_id=scene_id,
        chapter_id="C01",
        title="English title",
        learning_objective="Explain the English objective.",
        duration_target_seconds=5,
        narration="This canonical narration remains English.",
        visual_beats=[
            VisualBeat(
                beat_id=f"{scene_id}-B01",
                narration_segment="English narration segment",
                visual="English visible label",
                start_seconds=0,
                end_seconds=5,
            )
        ],
    )


def _maxwell_scene() -> SceneSpec:
    scene = _scene("S01")
    scene.equations = ["∇ · E = ρ / ε₀"]
    scene.narration = "Gauss's law for electricity is ∇ · E = ρ / ε₀."
    scene.visual_beats[0].visual = "Gauss's law: ∇ · E = ρ / ε₀"
    return scene


class DecidingPlanner:
    """Stands in for the planner model with a fixed decision for each turn."""

    name = "test"

    def __init__(self, **decision):
        self.decision = decision

    async def generate_structured(self, _prompt, schema, *, temperature):
        return schema(**{"canonical_message": "turn", **self.decision})

    async def generate_text(self, _prompt, **_kwargs):
        return "A written answer."


def _use_planner(monkeypatch, **decision):
    import scholarmotion.api.tutor as tutor_module

    planner = DecidingPlanner(**decision)
    monkeypatch.setattr(tutor_module, "create_llm_provider", lambda _settings: planner)


class TranslationProvider:
    """Marks the text as translated and keeps its placeholders, like a faithful translator."""

    name = "test"

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
        return "translated " + prompt.split("TEXT:\n", 1)[1]


def test_script_prompt_assigns_distinct_segment_purpose_and_prevents_repetition():
    item = CurriculumItem(
        title="Self-attention",
        objective="Explain how a query uses keys and values to build context.",
        duration_seconds=180,
    )
    prompt = _build_prompt(
        item,
        [],
        target_seconds=45,
        is_opening=False,
        is_closing=False,
        segment_index=3,
        segment_count=4,
        earlier_segments=["First, the phrase bank is ambiguous."],
    )

    assert "Work one small example from explicit values to a result" in prompt
    assert "Earlier narration in this lesson" in prompt
    assert "First, the phrase bank is ambiguous." in prompt
    assert "Do not introduce or prescribe an\nanalogy" in prompt
    assert "choose one accurate analogy" not in prompt


@pytest.mark.asyncio
async def test_script_equations_are_structured_and_reach_immutable_scene_specs():
    class StructuredScriptProvider:
        name = "test"

        async def generate_structured(self, _prompt, schema, *, temperature):
            if schema.__name__ == "ScriptReview":
                return schema(approved=True, problems=[])
            return schema(
                segments=[
                    {
                        "narration": "Gauss's law says charge creates electric flux.",
                        "equations": ["∇ · E = ρ / ε₀"],
                    }
                ]
            )

    curriculum = CurriculumPlan(
        topic="Gauss's law",
        items=[
            CurriculumItem(
                title="Gauss's law",
                objective="Explain electric flux.",
                duration_seconds=30,
            )
        ],
    )
    blocks = await write_script(StructuredScriptProvider(), curriculum, {"evidence": []})
    storyboard = {
        blocks[0].block_id: [
            VisualBeat(
                beat_id="b1",
                narration_segment=blocks[0].block_id,
                visual="Show the equation.",
                start_seconds=0,
                end_seconds=30,
            )
        ]
    }
    scene = compile_scene_specs(blocks, storyboard)[0]

    assert scene.equations == ["∇ · E = ρ / ε₀"]


@pytest.mark.asyncio
async def test_truncated_structured_lesson_cannot_publish_an_unreviewed_script():
    class MalformedProvider:
        name = "openai_compatible"

        async def generate_structured(self, *_args, **_kwargs):
            raise ValueError("provider returned truncated JSON")

    curriculum = CurriculumPlan(
        topic="the 4 Maxwell equations",
        items=[
            CurriculumItem(
                title="the 4 Maxwell equations",
                objective="Build a usable understanding of the 4 Maxwell equations.",
                duration_seconds=240,
            )
        ],
    )

    with pytest.raises(ValueError, match="truncated JSON"):
        await write_script(
            MalformedProvider(),
            curriculum,
            {"evidence": []},
            learner_request="Explain 4 Maxwell equations",
        )


@pytest.mark.asyncio
async def test_translation_localizes_derived_presentation_not_canonical_spec():
    canonical = _scene("S01")
    display, narration = await TranslationService(TranslationProvider()).localize_scene(
        canonical, text_language="Hindi", voice_language="Tamil"
    )
    assert canonical.title == "English title"
    assert canonical.narration == "This canonical narration remains English."
    assert display.title == "translated English title"
    assert display.visual_beats[0].visual.startswith("translated ")
    assert narration == "translated This canonical narration remains English."


@pytest.mark.asyncio
async def test_localized_text_strips_inline_math_markup_without_changing_symbols():
    class EchoProvider:
        name = "test"

        async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
            return prompt.split("TEXT:\n", 1)[1]

    canonical = _maxwell_scene()
    canonical.title = "Magnetic field ($B$)"
    canonical.visual_beats[0].visual = "Show the field ($B$) around a magnet."
    display, _ = await TranslationService(EchoProvider()).localize_scene(
        canonical, text_language="Tamil", voice_language="Tamil"
    )
    assert display.title == "Magnetic field (B)"
    assert display.visual_beats[0].visual == "Show the field (B) around a magnet."
    assert canonical.equations == ["∇ · E = ρ / ε₀"]


@pytest.mark.asyncio
async def test_localization_locks_equation_symbols_in_visible_text_and_narration():
    class TokenPreservingProvider:
        name = "test"

        async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
            return prompt.split("TEXT:\n", 1)[1].replace("Gauss's law", "காஸ் விதி")

    canonical = _maxwell_scene()
    display, narration = await TranslationService(TokenPreservingProvider()).localize_scene(
        canonical, text_language="Tamil", voice_language="Tamil"
    )

    assert "∇ · E = ρ / ε₀" in display.visual_beats[0].visual
    assert "∇ · E = ρ / ε₀" in narration
    assert canonical.visual_beats[0].visual == "Gauss's law: ∇ · E = ρ / ε₀"


@pytest.mark.asyncio
async def test_screen_text_is_translated_in_one_batch_and_equations_stay_locked():
    class BatchProvider:
        name = "test"

        def __init__(self):
            self.batches = 0
            self.texts = 0

        async def generate_structured(self, prompt, schema, *, temperature=None):
            self.batches += 1
            strings = json.loads(prompt.rsplit("\n", 1)[1])
            return schema(translations=[f"தமிழ் {value}" for value in strings])

        async def generate_text(self, prompt, *, temperature=None):
            self.texts += 1
            return "தமிழ் " + prompt.split("TEXT:\n", 1)[1]

    provider = BatchProvider()
    display, narration = await TranslationService(provider).localize_scene(
        _maxwell_scene(), text_language="Tamil", voice_language="Tamil"
    )

    # All screen strings in one structured call; the narration on its own.
    assert (provider.batches, provider.texts) == (1, 1)
    assert display.title.startswith("தமிழ் ")
    assert "∇ · E = ρ / ε₀" in display.visual_beats[0].visual
    assert "∇ · E = ρ / ε₀" in narration


@pytest.mark.asyncio
async def test_scene_scheduler_refills_slot_without_waiting_for_slowest_scene():
    started: list[str] = []
    finished: list[str] = []
    order: list[str] = []

    class ProbePipeline:
        max_concurrent_scenes = 2
        _run_scenes_as_ready = ScholarMotionPipeline._run_scenes_as_ready

        async def produce_scene(self, _project_id, spec, _events, **_kwargs):
            started.append(spec.scene_id)
            order.append(f"start:{spec.scene_id}")
            await asyncio.sleep({"S01": 0.06, "S02": 0.01, "S03": 0.01}[spec.scene_id])
            finished.append(spec.scene_id)
            order.append(f"finish:{spec.scene_id}")
            return spec.scene_id

    results = await ProbePipeline()._run_scenes_as_ready(
        "project",
        [_scene("S01"), _scene("S02"), _scene("S03")],
        [],
        known_source_ids=set(),
        corrections=[],
        code_skill_context="",
    )
    assert results == ["S01", "S02", "S03"]
    assert started[:2] == ["S01", "S02"]
    assert started[2] == "S03"
    assert order.index("finish:S02") < order.index("start:S03") < order.index("finish:S01")


@pytest.mark.asyncio
async def test_tutor_chat_persists_and_recognizes_timestamp_edit(monkeypatch):
    from scholarmotion.persistence.models import Scene

    _use_planner(
        monkeypatch,
        action="edit_video",
        edit_start_seconds=130,
        edit_end_seconds=150,
        edit_instruction="Change the example.",
    )
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Magnetic flux",
            request="Explain magnetic flux.",
            original_request="Explain magnetic flux.",
            text_language="English",
            voice_language="English",
            status="COMPLETE",
        )
        session.add(project)
        await session.flush()
        session.add(
            Scene(
                project_id=project.id,
                scene_key="S01",
                title="Flux",
                order_index=1,
                timeline_start=0,
                timeline_end=180,
            )
        )
        await session.flush()
        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="2:10 to 2:30, change the example."),
        )
        await session.commit()
        assert decision.action == "edit_range"
        assert decision.start == 130
        assert decision.end == 150
        assert decision.instruction == "Change the example."
        assert [message.role for message in await chat_history(session, project.id)] == [
            "user",
            "assistant",
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_text_only_chat_does_not_start_a_video_workflow():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Linear algebra chat",
            request="Explain why a matrix can rotate a vector.",
            original_request="Explain why a matrix can rotate a vector.",
            mode="chat",
            text_language="English",
            voice_language="English",
            status="CREATED",
        )
        session.add(project)
        await session.flush()
        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="Can you start from the intuition? Text only, no video."),
        )
        await session.commit()
        assert decision.action == "answer"
        assert "matrix" in decision.canonical_message.lower()
        assert [message.role for message in await chat_history(session, project.id)] == [
            "user",
            "assistant",
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_student_question_gets_a_written_answer_by_default():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Calculus course",
            request="Build intuition for derivatives.",
            original_request="Build intuition for derivatives.",
            mode="both",
            text_language="English",
            voice_language="English",
            status="CREATED",
        )
        session.add(project)
        await session.flush()
        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="Why does the derivative measure slope?"),
        )
        assert decision.action == "answer"
        assert project.text_language == "English"
    await engine.dispose()


@pytest.mark.asyncio
async def test_follow_up_question_stays_in_chat_after_text_only_turn(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Paper Q&A",
            request="Text-only Q&A about the attached paper.",
            original_request="Text-only Q&A about the attached paper.",
            mode="chat",
            text_language="English",
            voice_language="English",
            status="CREATED",
        )
        session.add(project)
        await session.flush()
        session.add_all(
            [
                TutorMessage(
                    project_id=project.id,
                    role="user",
                    canonical_text="On which page is Figure 2?",
                    display_text="On which page is Figure 2?",
                    language="English",
                ),
                TutorMessage(
                    project_id=project.id,
                    role="assistant",
                    canonical_text="Figure 2 is on page 4.",
                    display_text="Figure 2 is on page 4.",
                    language="English",
                    action="answer",
                ),
            ]
        )
        await session.flush()

        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="How does the decoder prevent future-token leakage?"),
        )

        assert decision.action == "answer"
        assert project.status == "CREATED"
        assert project.mode == "chat"

        _use_planner(monkeypatch, action="generate_video", canonical_request="Explain the decoder.")
        explicit_video = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="Please make a video explaining the decoder."),
        )
        assert explicit_video.action == "generate_video"
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_educational_request_respects_chat_mode_when_planner_is_offline():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Maxwell equations",
            request="Explain Maxwell's equations.",
            original_request="Explain Maxwell's equations.",
            mode="chat",
            text_language="English",
            voice_language="English",
            status="CREATED",
        )
        session.add(project)
        await session.flush()
        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="Explain Maxwell's four equations, in Tamil."),
        )

        assert decision.action == "answer"
        assert project.mode == "chat"
        # Without the planner nothing is inferred from the words; saved languages stay.
        assert project.text_language == "English"
        assert project.voice_language == "English"
    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_request_can_naturally_start_a_video_without_a_mode_toggle(monkeypatch):
    _use_planner(monkeypatch, action="generate_video", canonical_request="Wave interference.")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = Project(
            title="Wave interference",
            request="Explain wave interference.",
            original_request="Explain wave interference.",
            mode="chat",
            text_language="English",
            voice_language="English",
            status="CREATED",
        )
        session.add(project)
        await session.flush()
        decision = await respond(
            session,
            _offline_settings(),
            project,
            TutorChatRequest(message="Please make a visual lesson video for this."),
        )
        assert decision.action == "generate_video"
        assert project.mode == "both"
    await engine.dispose()
