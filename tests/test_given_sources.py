"""Sources the learner gives in chat: read them, ask before adding outside material, obey."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.agents.lesson_preparation import LessonPreparation, _validated_sources
from scholarmotion.api import service as service_api
from scholarmotion.api import tutor as tutor_api
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, SourceDocument
from scholarmotion.retrieval.source_ingestion import LinkUnreadable
from scholarmotion.retrieval.web_page import parse_web_page
from scholarmotion.schemas import SceneSpec, TutorChatRequest, VisualBeat
from scholarmotion.services.orchestration import ScholarMotionPipeline, _scenes_to_repair

BLOG = "https://example-blog.blogspot.com/2026/10/consciousness.html"

# Blogger-style markup: the post sits inside a "widget" wrapper, uses <br> lines,
# and the page carries a sidebar and comments around it.
BLOGGER_PAGE = f"""<!doctype html><html><head><title>Ignored</title>
<meta property="og:title" content="Consciousness in AI models?"></head><body>
<nav>Home About</nav>
<div class="widget Blog"><article class="post-outer-container"><div class="post-body">
<p>In my view, looking for consciousness in AI models is looking in the wrong place.
Consciousness is the witness, not the activity of the mind.</p>
<h3>Energy as an analogy</h3>
<div>We understand energy through its effects such as heat, light and motion.<br>
We infer it from what it does, never by seeing it directly, and that is the point.</div>
<p>{"Language models show sophisticated expressions of a mind. " * 6}</p>
</div><div class="comments">Nice post! Great read.</div></article></div>
<aside class="sidebar">Popular posts</aside><script>var x = 1;</script>
</body></html>"""


def test_a_blog_post_is_read_as_its_main_text():
    page = parse_web_page(BLOGGER_PAGE, BLOG)
    text = " ".join(chunk.text for chunk in page.chunks)
    assert page.title == "Consciousness in AI models?"
    assert "looking for consciousness in AI models" in text
    assert "heat, light and motion" in text
    assert any(
        c.content_type == "section" and c.text == "Energy as an analogy" for c in page.chunks
    )
    for furniture in ("Popular posts", "Nice post", "Home About", "var x"):
        assert furniture not in text


def test_a_page_without_readable_text_is_reported():
    with pytest.raises(ValueError, match="no readable article text"):
        parse_web_page(
            "<html><body><div id='app'></div><script>render()</script></body></html>", BLOG
        )


def test_only_links_really_in_the_chat_are_kept():
    result = _validated_sources(
        LessonPreparation(source_links=[BLOG + ").", "https://invented.example.com/x"]),
        f"USER: take this blog ({BLOG}).",
        has_attached=False,
    )
    assert result.source_links == [BLOG]
    # A link the learner pointed at is given material; its use is not "none".
    assert result.source_scope == "not_said"


def test_without_given_material_there_is_no_source_scope():
    result = _validated_sources(
        LessonPreparation(source_scope="only_given"), "USER: explain entropy", has_attached=False
    )
    assert result.source_scope == "none"


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


async def _new_chat(session, *, with_source: bool = False) -> Project:
    project = Project(title="Blog", request="r", original_request="r", mode="chat")
    session.add(project)
    await session.flush()
    if with_source:
        session.add(
            SourceDocument(
                project_id=project.id,
                kind="web_page",
                title="Consciousness in AI models?",
                uri=f"{project.id}/sources/consciousness.html",
                metadata_json={"source_url": BLOG, "ingestion_status": "complete"},
            )
        )
        await session.flush()
    return project


async def _respond(monkeypatch, session, project, message, **decision):
    monkeypatch.setattr(tutor_api, "TranslationService", _Echo)
    monkeypatch.setattr(tutor_api, "create_translation_provider", lambda _settings: object())
    monkeypatch.setattr(tutor_api, "create_llm_provider", lambda _settings: _Planner(**decision))
    return await tutor_api.respond(session, Settings(), project, TutorChatRequest(message=message))


@pytest.mark.asyncio
async def test_a_video_from_a_given_source_asks_before_adding_outside_material(
    monkeypatch, session
):
    project = await _new_chat(session)

    async def read(_session, _settings, _store, _project_id, url):
        assert url == BLOG
        return SimpleNamespace(title="Consciousness in AI models?")

    monkeypatch.setattr(tutor_api, "ingest_link", read)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        f"{BLOG} turn this blog into a video",
        action="generate_video",
        source_links=[BLOG],
        source_scope="not_said",
    )
    assert decision.action == "answer"
    assert '"Consciousness in AI models?"' in decision.canonical_message
    assert "only this source" in decision.canonical_message
    assert project.mode == "chat"  # no video was started


@pytest.mark.asyncio
async def test_the_learners_answer_starts_the_video(monkeypatch, session):
    project = await _new_chat(session, with_source=True)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        "only the blog",
        action="generate_video",
        source_scope="only_given",
        canonical_request="Explain the blog post 'Consciousness in AI models?'.",
    )
    assert decision.action == "generate_video"
    assert project.mode == "both"


@pytest.mark.asyncio
async def test_an_unreadable_link_is_reported_not_replaced(monkeypatch, session):
    project = await _new_chat(session)

    async def unreadable(*_args):
        raise LinkUnreadable(BLOG, "the page needs a login or subscription")

    monkeypatch.setattr(tutor_api, "ingest_link", unreadable)
    decision = await _respond(
        monkeypatch,
        session,
        project,
        f"make a video of {BLOG}",
        action="generate_video",
        source_links=[BLOG],
        source_scope="given_plus_web",
    )
    assert decision.action == "answer"
    assert "couldn't read" in decision.canonical_message
    assert "needs a login" in decision.canonical_message
    assert project.mode == "chat"


@pytest.mark.asyncio
async def test_a_short_given_source_is_used_whole_in_reading_order():
    chunks = [{"id": f"c{i}", "document_id": "d", "text": f"part {i}"} for i in range(5)]
    assert await service_api.given_source_chunks(None, chunks, "query") == chunks


@pytest.mark.asyncio
async def test_a_long_given_source_keeps_reading_order(monkeypatch):
    chunks = [{"id": f"c{i:03d}", "document_id": "d", "text": f"part {i}"} for i in range(80)]

    class Retriever:
        def __init__(self, _embeddings):
            pass

        async def search(self, _query, values, limit):
            picked = [values[70], values[3], values[40]]
            return [SimpleNamespace(chunk=item, score=0.9) for item in picked]

    monkeypatch.setattr(service_api, "HybridRetriever", Retriever)
    selected = await service_api.given_source_chunks(None, chunks, "query")
    assert [chunk["id"] for chunk in selected] == ["c003", "c040", "c070"]


def _scene(scene_id: str, narration: str, visual: str = "a diagram"):
    return SimpleNamespace(
        spec=SceneSpec(
            scene_id=scene_id,
            chapter_id="C01",
            title=scene_id,
            learning_objective="objective",
            duration_target_seconds=30,
            visual_beats=[VisualBeat(beat_id="B1", narration_segment=narration, visual=visual)],
            narration=narration,
        ),
        audio=SimpleNamespace(duration_seconds=30.0),
        report=SimpleNamespace(passed=True),
        render_version=2,
        code_version=1,
        audio_version=1,
    )


def test_only_blocking_issues_tied_to_a_real_scene_are_repaired():
    scenes = [_scene("S01", "a"), _scene("S02", "b")]
    review = {
        "issues": [
            {
                "scene_id": "S01",
                "problem": "says the blog is unverified",
                "blocking": True,
                "fix": "Remove the remark about verification.",
            },
            {"scene_id": "S02", "problem": "transition is a bit abrupt", "blocking": False},
            {"scene_id": "S09", "problem": "unknown scene", "blocking": True, "fix": "x"},
        ]
    }
    assert _scenes_to_repair(review, scenes) == {"S01": "Remove the remark about verification."}


@pytest.mark.asyncio
async def test_the_lesson_review_sees_full_visual_plans_and_names_scenes():
    long_visual = "A labelled diagram " + "with many careful details " * 30
    seen = {}

    class Reviewer:
        name = "test"

        async def generate_structured(self, prompt, schema, *, temperature=None):
            seen["prompt"] = prompt
            return schema(
                coherent=False,
                natural_ending=True,
                issues=[{"scene_id": "S01", "problem": "meta note", "blocking": True, "fix": "f"}],
            )

    pipeline = SimpleNamespace(_planner=lambda: Reviewer(), _source_scope="only_given")
    review = await ScholarMotionPipeline._review_lesson_flow(
        pipeline, [_scene("S01", "narration", long_visual)]
    )
    assert long_visual in seen["prompt"]
    assert "SOURCE POLICY" in seen["prompt"]
    assert review["problems"] == ["S01: meta note"]


@pytest.mark.asyncio
async def test_a_review_repair_rebuilds_the_scene_with_revised_narration():
    produced = {}

    class Writer:
        async def generate_text(self, _prompt):
            return "The author argues that consciousness is the witness behind the mind."

    async def produce_scene(project_id, spec, events, **kwargs):
        produced.update(spec=spec, version=kwargs["artifact_version"])
        return SimpleNamespace(spec=spec)

    async def notify(*_args, **_kwargs):
        return None

    pipeline = SimpleNamespace(llm=Writer(), produce_scene=produce_scene, _notify_progress=notify)
    original = _scene("S01", "We could not verify this blog, but here is what it says.")
    untouched = _scene("S02", "second")
    scenes, repaired = await ScholarMotionPipeline._repair_scenes(
        pipeline,
        "p",
        [original, untouched],
        {"S01": "Remove the remark about verification."},
        [],
        known_source_ids=set(),
        code_skill_context="",
    )
    assert repaired == ["S01"]
    assert scenes[1] is untouched
    assert produced["spec"].version == 2 and "witness" in produced["spec"].narration
    assert produced["version"] == 3  # after the scene's newest render


@pytest.mark.asyncio
async def test_a_failed_review_repair_keeps_the_original_scene():
    class Writer:
        async def generate_text(self, _prompt):
            return "A rewritten narration."

    async def produce_scene(*_args, **_kwargs):
        raise RuntimeError("render failed")

    async def notify(*_args, **_kwargs):
        return None

    pipeline = SimpleNamespace(llm=Writer(), produce_scene=produce_scene, _notify_progress=notify)
    original = _scene("S01", "old")
    scenes, repaired = await ScholarMotionPipeline._repair_scenes(
        pipeline, "p", [original], {"S01": "fix"}, [], known_source_ids=set(), code_skill_context=""
    )
    assert scenes == [original] and repaired == []
