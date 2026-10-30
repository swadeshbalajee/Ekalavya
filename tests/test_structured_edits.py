"""Edits as per-scene operations: change, add, remove, split, merge, move, undo."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.lesson_preparation import LessonPreparation, SceneEdit, _validated
from scholarmotion.agents.script_writer import ScriptSegment, review_transitions
from scholarmotion.api import service as service_api
from scholarmotion.api.tutor import edit_plan_for
from scholarmotion.config import Settings
from scholarmotion.media.pronunciation import respell, restore_spelling
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import (
    AudioArtifact,
    GeneratedCode,
    Project,
    ProjectArtifact,
    ProjectRevision,
    RenderArtifact,
    Scene,
    SceneSpecification,
    TimelineSegment,
)
from scholarmotion.schemas import (
    FeedbackClassification,
    FeedbackType,
    Pause,
    SceneSpec,
    TimingCue,
    TTSResult,
    VerificationReport,
    VisualBeat,
)
from scholarmotion.services.edit_plan import EditPlan
from scholarmotion.services.orchestration import ProducedScene
from scholarmotion.verification.checklist import edit_check, score_checklist


def _spec(key: str, narration: str, **extra) -> SceneSpec:
    return SceneSpec(
        scene_id=key,
        chapter_id="C01",
        title=f"Scene {key}",
        learning_objective="Teach",
        duration_target_seconds=20,
        narration=narration,
        visual_beats=[VisualBeat(beat_id="b", narration_segment=narration, visual="x")],
        **extra,
    )


def _slots(*keys):
    return [
        service_api._EditSlot(key, _spec(key, f"{key} one. {key} two."), object()) for key in keys
    ]


def _keys():
    counter = iter(range(10, 99))
    return lambda: f"S{next(counter)}"


def test_operations_rearrange_scenes_and_mark_what_is_rebuilt():
    plan = EditPlan(
        edits=[
            SceneEdit(op="change", scene="S01", instruction="simpler"),
            SceneEdit(op="change", scene="S04", instruction="add a pause before the answer"),
            SceneEdit(op="delete", scene="S02"),
            SceneEdit(op="insert_after", scene="S03", instruction="a worked example"),
            SceneEdit(op="move_after", scene="S01", after="S03"),
        ]
    )
    slots = service_api._arrange(_slots("S01", "S02", "S03", "S04"), plan, _keys())
    order = [slot.key for slot in slots]
    # S03 first, S01 moved after it, then the new scene, then S04; S02 is gone.
    assert order[:2] == ["S03", "S01"] and order[-1] == "S04" and "S02" not in order
    new = slots[2]
    assert new.row is None and new.request == "a worked example" and new.rebuild
    # Non-adjacent changes rebuild exactly those scenes, each with its own request.
    assert [s.key for s in slots if s.rebuild] == ["S01", new.key, "S04"]
    assert slots[1].request == "simpler" and slots[3].request.startswith("add a pause")


def test_split_and_merge_keep_the_words_and_the_pauses():
    first = service_api._EditSlot(
        "S01",
        _spec(
            "S01",
            "One two three. Four five. Six seven eight. Nine.",
            pauses=[Pause(after_sentence=2, seconds=3)],
        ),
        object(),
    )
    split = service_api._arrange(
        [first], EditPlan(edits=[SceneEdit(op="split", scene="S01")]), _keys()
    )
    assert [slot.spec.narration for slot in split] == [
        "One two three. Four five.",
        "Six seven eight. Nine.",
    ]
    assert split[1].spec.pauses == [Pause(after_sentence=0, seconds=3)] and split[1].row is None

    merged = service_api._merged(split[0].spec, split[1].spec)
    assert merged.narration == "One two three. Four five. Six seven eight. Nine."
    assert merged.pauses == [Pause(after_sentence=2, seconds=3)]


def test_a_scene_added_after_one_this_edit_removes_takes_its_place():
    plan = EditPlan(
        edits=[
            SceneEdit(op="delete", scene="S03"),
            SceneEdit(op="insert_after", scene="S03", instruction="why sunsets look red"),
        ]
    )
    slots = service_api._arrange(_slots("S01", "S02", "S03"), plan, _keys())
    assert [slot.key for slot in slots][:2] == ["S01", "S02"] and len(slots) == 3
    assert slots[2].row is None and slots[2].request == "why sunsets look red"


def test_a_bridge_fix_rewrites_only_the_opening_and_keeps_later_pauses():
    spec = _spec(
        "S02",
        "Old opening. Second line. Third line. Is it blue? Yes.",
        pauses=[Pause(after_sentence=0, seconds=1), Pause(after_sentence=3, seconds=2)],
    )
    bridged = service_api._with_opening(
        spec, "So, after that, here is why. Then a bridge. And more."
    )
    # Two sentences out, three in: the rest is word for word and its pause moves by one.
    assert bridged.narration == (
        "So, after that, here is why. Then a bridge. And more. Third line. Is it blue? Yes."
    )
    assert bridged.pauses == [Pause(after_sentence=4, seconds=2)]


def test_scenes_named_apart_are_changed_apart_not_everything_between():
    scenes = [
        SimpleNamespace(scene_key=f"S0{i}", timeline_start=10.0 * (i - 1), timeline_end=10.0 * i)
        for i in range(1, 6)
    ]
    preparation = LessonPreparation(
        action="edit_video", target_scenes=["S02", "S05"], edit_instruction="slower"
    )
    plan = edit_plan_for(preparation, scenes, "slower")
    # The old range editor rebuilt S02 to S05; the plan names only the two scenes.
    assert [edit.scene for edit in plan.edits] == ["S02", "S05"]
    own = LessonPreparation(
        action="edit_video",
        edits=[
            SceneEdit(scene="S02", instruction="slower"),
            SceneEdit(scene="S05", instruction="add pauses"),
        ],
    )
    plan = edit_plan_for(own, scenes, "")
    assert [(e.scene, e.instruction) for e in plan.edits] == [
        ("S02", "slower"),
        ("S05", "add pauses"),
    ]
    assert plan.describe({"S02": 2, "S05": 5}) == "update scene 2, update scene 5"


def test_the_planner_cannot_name_scenes_that_do_not_exist_or_undo_to_nothing():
    fallback = LessonPreparation()
    scenes = [{"id": "S01", "start": 0, "end": 5}, {"id": "S02", "start": 5, "end": 9}]
    result = _validated(
        LessonPreparation(
            action="edit_video",
            edits=[
                SceneEdit(scene="S09", instruction="x"),
                SceneEdit(op="insert_after", scene="", instruction="an intro"),
                SceneEdit(op="insert_after", scene="S01", instruction=""),
                SceneEdit(op="move_after", scene="S02", after="S07"),
                SceneEdit(op="delete", scene="S02"),
            ],
            pronunciations={"Euler": "Oiler", "same": "same"},
        ),
        fallback,
        scenes,
        [],
    )
    assert [(e.op, e.scene) for e in result.edits] == [("insert_after", ""), ("delete", "S02")]
    assert result.pronunciations == {"Euler": "Oiler"}
    undo = _validated(LessonPreparation(action="restore_video"), fallback, scenes, [], [])
    assert undo.action == "answer"


def test_a_plural_is_respelled_and_restored_like_its_word():
    from scholarmotion.media.pronunciation import uses_any

    assert uses_any("Short wavelengths scatter.", {"wavelength": "wave-length"})
    said = respell("Short wavelengths, one wavelength.", {"wavelength": "wave-length"})
    assert said == "Short wave-lengths, one wave-length."
    assert not uses_any("Wavelengthy prose.", {"wavelength": "wave-length"})


def test_pronunciation_changes_the_recording_not_the_subtitles():
    said = respell("Euler's formula, by EULER.", {"Euler": "Oiler"})
    assert said == "Oiler's formula, by Oiler."
    audio = TTSResult(
        audio_path="a.wav",
        duration_seconds=2,
        sentence_timings=[TimingCue(text=said, start=0, end=2)],
    )
    assert restore_spelling(audio, {"Euler": "Oiler"}).sentence_timings[0].text == (
        "Euler's formula, by Euler."
    )


def test_an_edited_scene_must_show_the_change():
    item = edit_check("make the graph blue")
    answers = [
        {"id": "edit_done", "passed": False, "description": "still red", "suggested_repair": ""}
    ]
    result = score_checklist("S01", answers, (item,))
    assert not result.passed and result.failed == ["edit_done"]


@pytest.mark.asyncio
async def test_transition_fixes_only_name_checked_boundaries():
    class Reviewer:
        name = "x"

        async def generate_structured(self, prompt, schema):
            assert "BOUNDARY 1" in prompt and "S03" in prompt
            return schema(
                fixes=[{"scene": "S03", "fix": "bridge from S01"}, {"scene": "S09", "fix": "x"}]
            )

    scenes = [("S01", "a."), ("S03", "b."), ("S04", "c.")]
    assert await review_transitions(Reviewer(), scenes, [1]) == {"S03": "bridge from S01"}


@pytest.fixture
async def database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def sessions():
        async with maker() as session:
            yield session

    yield SimpleNamespace(sessions=sessions)
    await engine.dispose()


async def _video(database) -> Project:
    async with database.sessions() as session:
        project = Project(
            title="Factors",
            request="factors",
            original_request="factors",
            mode="both",
            status="COMPLETE",
            text_language="English",
            voice_language="English",
        )
        session.add(project)
        await session.flush()
        session.add(
            ProjectArtifact(project_id=project.id, kind="video", version=1, uri="v1", payload={})
        )
        for index, key in enumerate(["S01", "S02", "S03"]):
            scene = Scene(
                project_id=project.id,
                scene_key=key,
                title=key,
                order_index=index,
                timeline_start=index * 10.0,
                timeline_end=index * 10.0 + 10,
                active_code_version=1,
                active_audio_version=1,
                active_render_version=1,
            )
            session.add(scene)
            await session.flush()
            session.add_all(
                [
                    SceneSpecification(
                        scene_id=scene.id,
                        version=1,
                        payload=_spec(key, f"Part {key} begins. Part {key} ends.").model_dump(
                            mode="json"
                        ),
                        uri="s",
                    ),
                    GeneratedCode(
                        scene_id=scene.id, version=1, spec_version=1, uri="c", scene_class="S"
                    ),
                    AudioArtifact(
                        scene_id=scene.id,
                        version=1,
                        narration_version=1,
                        uri=f"{key}.wav",
                        duration=10,
                        timings=[{"text": "x", "start": 0, "end": 10}],
                    ),
                    RenderArtifact(
                        scene_id=scene.id,
                        version=1,
                        code_version=1,
                        audio_version=1,
                        uri=f"{key}.mp4",
                    ),
                    TimelineSegment(
                        project_id=project.id,
                        video_version=1,
                        scene_id=scene.id,
                        start_seconds=index * 10.0,
                        end_seconds=index * 10.0 + 10,
                        render_version=1,
                    ),
                ]
            )
        await session.commit()
        return project


class _Runtime:
    """The pipeline parts an edit uses, with every model and render faked."""

    def __init__(self, tmp_path):
        self.produced = []
        self.storage = SimpleNamespace(
            local_path=lambda key: tmp_path / key, put_bytes=lambda *a, **k: None
        )
        self.assembler = SimpleNamespace(
            assemble=lambda renders, output, **_: SimpleNamespace(
                success=True, ordered_inputs=renders, log=""
            ),
            probe_duration=lambda _path: 10.0,
        )
        self.skills = SimpleNamespace(context_for=self._skills)
        self.correction_folder = None

        class Writer:
            name = "test"

            async def generate_structured(self, prompt, schema, **_):
                if schema is ScriptSegment:
                    return ScriptSegment(
                        narration="Here is a worked example. [pause 2s] It works.", title="Example"
                    )
                return schema()

            async def generate_text(self, prompt, **_):
                return "Simpler words now. The end."

        self.llm = Writer()

    async def _skills(self, _role):
        return ""

    def _planner(self):
        return self.llm

    def _json(self, *_args, **_kwargs):
        return ""

    async def produce_scene(self, project_id, spec, **kwargs):
        self.produced.append((spec, kwargs))
        audio = kwargs.get("existing_audio") or TTSResult(
            audio_path=f"{spec.scene_id}-new.wav",
            duration_seconds=12,
            sentence_timings=[TimingCue(text=spec.narration, start=0, end=12)],
        )
        return ProducedScene(
            spec,
            "code.py",
            audio,
            f"{spec.scene_id}-new.mp4",
            VerificationReport(
                scene_id=spec.scene_id, passed=True, score=1.0, issues=[], layers={}
            ),
            render_version=7,
        )


@pytest.mark.asyncio
async def test_an_edit_rebuilds_only_its_scenes_and_saves_one_new_version(
    database, tmp_path, monkeypatch
):
    project = await _video(database)
    runtime = _Runtime(tmp_path)
    monkeypatch.setattr(service_api, "create_pipeline", lambda *a, **k: runtime)

    async def narration_profile(*_args):
        return {}

    async def classify(_planner, instruction):
        return [
            FeedbackClassification(feedback_type=FeedbackType.CONTENT_EDIT, instruction=instruction)
        ]

    async def storyboard(_planner, blocks, **_):
        return {
            block.block_id: [VisualBeat(beat_id="n", narration_segment=block.text, visual="new")]
            for block in blocks
        }

    monkeypatch.setattr(service_api, "_narration_profile", narration_profile)
    monkeypatch.setattr(service_api, "classify_feedback", classify)
    monkeypatch.setattr(service_api, "plan_storyboard", storyboard)
    plan = EditPlan(
        edits=[
            SceneEdit(op="change", scene="S01", instruction="simpler"),
            SceneEdit(op="delete", scene="S02"),
            SceneEdit(op="insert_after", scene="S03", instruction="a worked example"),
        ],
        summary="simplify, drop scene 2, add an example",
    )
    affected = await service_api.edit_range(database, Settings(), project.id, 0, 0, plan.encode())

    assert affected == ["S01", "S04"]  # S03 is untouched; S02 is removed, not rebuilt
    edited, new = (spec for spec, _ in runtime.produced)
    assert edited.narration == "Simpler words now. The end." and edited.version == 2
    assert new.scene_id == "S04" and new.pauses == [Pause(after_sentence=0, seconds=2)]
    assert "pause" not in new.narration and new.visual_beats[0].visual == "new"
    assert runtime.produced[1][1]["edit_request"] == "New scene: a worked example"
    async with database.sessions() as session:
        rows = {s.scene_key: s for s in (await session.scalars(select(Scene))).all()}
        assert rows["S02"].timeline_start is None
        assert rows["S01"].verification_state == rows["S04"].verification_state == "PASSED"
        current = sorted(
            (s for s in rows.values() if s.timeline_start is not None), key=lambda s: s.order_index
        )
        assert [s.scene_key for s in current] == ["S01", "S03", "S04"]
        v2 = (
            await session.scalars(select(TimelineSegment).where(TimelineSegment.video_version == 2))
        ).all()
        assert len(v2) == 3
        revision = await session.scalar(
            select(ProjectRevision).where(ProjectRevision.active.is_(True))
        )
        assert revision.payload["removed_scenes"] == ["S02"]

        # Undo: version 1 comes back with its three scenes in their old order.
        project_row = await session.get(Project, project.id)
        assert await service_api.activate_video_version(session, project_row, 1)
        restored = sorted(
            (
                s
                for s in (await session.scalars(select(Scene))).all()
                if s.timeline_start is not None
            ),
            key=lambda s: s.order_index,
        )
        assert [s.scene_key for s in restored] == ["S01", "S02", "S03"]
        await session.commit()

    # An edit after the undo numbers its versions past the undone ones, not into them.
    runtime.produced.clear()
    again = EditPlan(edits=[SceneEdit(op="change", scene="S01", instruction="simpler")])
    assert await service_api.edit_range(database, Settings(), project.id, 0, 0, again.encode()) == [
        "S01"
    ]
    assert runtime.produced[0][0].version == 3


@pytest.mark.asyncio
async def test_a_failed_scene_leaves_the_video_unchanged(database, tmp_path, monkeypatch):
    project = await _video(database)
    runtime = _Runtime(tmp_path)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("render crashed")

    runtime.produce_scene = broken
    monkeypatch.setattr(service_api, "create_pipeline", lambda *a, **k: runtime)

    async def narration_profile(*_args):
        return {}

    monkeypatch.setattr(service_api, "_narration_profile", narration_profile)
    plan = EditPlan(edits=[SceneEdit(op="delete", scene="S02"), SceneEdit(op="split", scene="S01")])
    with pytest.raises(RuntimeError):
        await service_api.edit_range(database, Settings(), project.id, 0, 0, plan.encode())
    async with database.sessions() as session:
        keys = sorted(
            s.scene_key
            for s in (await session.scalars(select(Scene))).all()
            if s.timeline_start is not None
        )
        assert keys == ["S01", "S02", "S03"]


def test_a_word_the_lesson_never_says_is_not_respelled_or_guessed_into_a_scene():
    from scholarmotion.api.tutor import checked_pronunciations

    narrations = {
        "S01": "Euler found it.",
        "S02": "Tiny gas molecules scatter light.",
        "S03": "So the sky is blue.",
    }
    plan = EditPlan(
        edits=[
            SceneEdit(op="change", scene="S02", instruction="Say Rayleigh as ray-lee."),
            SceneEdit(op="change", scene="S01", instruction="Say Euler as Oiler."),
            SceneEdit(op="change", scene="S03", instruction="slower"),
        ],
        pronunciations={"Rayleigh": "ray-lee", "Euler": "Oiler"},
    )
    checked, saying, missing = checked_pronunciations(plan, narrations)
    # Rayleigh is never said: no respelling, and the guessed scene is left alone.
    assert checked.pronunciations == {"Euler": "Oiler"} and missing == ["Rayleigh"]
    assert saying == ["S01"]
    # Respelling rebuilds S01 by itself; only the unrelated change stays.
    assert [edit.scene for edit in checked.edits] == ["S03"]

    only = EditPlan(
        edits=[SceneEdit(op="change", scene="S02", instruction="Say Rayleigh as ray-lee.")],
        pronunciations={"Rayleigh": "ray-lee"},
    )
    checked, saying, missing = checked_pronunciations(only, narrations)
    assert not checked.edits and not checked.pronunciations and missing == ["Rayleigh"]
