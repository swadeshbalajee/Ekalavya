"""Conversation actions for the learner-facing tutor UI."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.agents.lesson_preparation import prepare_lesson
from scholarmotion.config import Settings
from scholarmotion.persistence.models import (
    Project,
    ProjectArtifact,
    Scene,
    SourceChunk,
    SourceDocument,
    TimelineSegment,
    TutorMessage,
    UserAccount,
    WorkspaceProfile,
)
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_llm_provider,
    create_machine_translator,
    create_translation_provider,
)
from scholarmotion.retrieval.project_papers import project_papers, project_sources
from scholarmotion.retrieval.source_ingestion import LinkUnreadable, ingest_link
from scholarmotion.retrieval.web_research import format_sources, supports_web_search
from scholarmotion.schemas import TutorChatRequest
from scholarmotion.translation import TranslationService
from scholarmotion.verification.answers import answer_problems

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TutorDecision:
    message: str
    canonical_message: str
    action: str
    start: float | None = None
    end: float | None = None
    instruction: str | None = None
    # An earlier video version to restore before acting, when the learner meant that one.
    base_version: int | None = None


def is_active(message: TutorMessage) -> bool:
    """False for messages an edit replaced; they stay viewable as an older branch."""
    return not (message.metadata_json or {}).get("superseded")


async def chat_history(
    session: AsyncSession, project_id, *, include_superseded: bool = False
) -> list[TutorMessage]:
    messages = (
        await session.scalars(
            select(TutorMessage)
            .where(TutorMessage.project_id == project_id)
            .order_by(TutorMessage.created_at)
        )
    ).all()
    return [m for m in messages if include_superseded or is_active(m)]


async def _paper_context(
    session: AsyncSession, project: Project, query: str, *, limit: int = 6
) -> str:
    rows = (
        await session.execute(
            select(SourceChunk, SourceDocument)
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(SourceDocument.project_id == project.id)
        )
    ).all()
    library = not rows
    if library:
        # No attached paper: search the shared library (for example NCERT).
        rows = (
            await session.execute(
                select(SourceChunk, SourceDocument)
                .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                .where(SourceDocument.project_id.is_(None))
                .limit(5000)
            )
        ).all()
    if not rows:
        return NO_SOURCES

    stop_words = {
        "about",
        "after",
        "again",
        "also",
        "does",
        "explain",
        "from",
        "have",
        "into",
        "just",
        "more",
        "please",
        "that",
        "them",
        "then",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
    }
    terms = {
        word
        for word in re.findall(r"[a-z0-9]+", query.casefold())
        if len(word) > 2 and word not in stop_words
    }
    ranked = []
    for chunk, document in rows:
        text = chunk.text.strip()
        text_terms = set(re.findall(r"[a-z0-9]+", text.casefold()))
        overlap = len(terms & text_terms)
        phrase = int(bool(query.strip()) and query.casefold() in text.casefold())
        ranked.append((overlap, phrase, chunk, document, text))
    ranked.sort(key=lambda row: (row[0], row[1], -len(row[4])), reverse=True)
    # Library matches need real topical overlap; a stray shared word is noise.
    minimum = max(2, len(terms) // 2) if library else 1
    selected = [row for row in ranked if row[0] >= minimum or row[1]][:limit]
    if not selected:
        if library:
            return NO_SOURCES
        selected = ranked[: min(3, limit)]

    excerpts = []
    for _, _, chunk, document, text in selected:
        excerpt = " ".join(text.split())[:900]
        provenance = f"{document.title}, p. {chunk.page or '?'}"
        if chunk.section:
            provenance += f", {chunk.section}"
        excerpts.append(f"[{provenance}] {excerpt}")
    return "\n\n".join(excerpts)


NO_SOURCES = "(no paper sources are attached to this conversation)"


def _fallback_tutor_answer(project: Project, question: str, paper_context: str) -> str:
    """Give a bounded lesson-aware answer when every configured LLM is unavailable."""
    context = paper_context.strip()
    citations = re.findall(r"\[([^\]]+,\s*p\.\s*\d+)(?:, [^\]]+)?\]", context)
    citation = f" [{citations[0]}]" if citations else ""

    if context:
        first_excerpt = re.sub(r"\[[^\]]+\]\s*", "", context.split("\n\n", 1)[0]).strip()
        return (
            "The tutor model is temporarily unavailable, so I can only ground this answer in the "
            f"retrieved material: {first_excerpt[:500]}{citation}"
        )
    return (
        "The tutor model is temporarily unavailable, and I don't have a retrieved passage that "
        "supports a reliable answer right now. Your video and saved chat are still available; "
        "please retry this question shortly."
    )


async def respond(
    session: AsyncSession,
    settings: Settings,
    project: Project,
    payload: TutorChatRequest,
) -> TutorDecision:
    """Route one chat turn by the planner model's decision.

    The planner reads the learner's words; this function only checks the
    decision against the conversation's real state (video ready, scenes,
    duration) and starts the matching action.
    """
    translator = TranslationService(
        create_translation_provider(settings), create_machine_translator(settings)
    )
    prior_messages = list(
        (
            await session.scalars(
                select(TutorMessage)
                .where(TutorMessage.project_id == project.id)
                .order_by(TutorMessage.created_at.desc())
                .limit(40)
            )
        ).all()
    )
    prior_messages = [message for message in prior_messages if is_active(message)][:12]
    prior_messages.reverse()
    conversation = "\n".join(
        f"{message.role.upper()}: {message.canonical_text}" for message in prior_messages
    )
    # Conversation history is ordered by the learner's most recent turn, not
    # merely by when the lesson was first created.
    project.updated_at = datetime.now(UTC)
    user_message = TutorMessage(
        project_id=project.id,
        role="user",
        canonical_text=payload.message,
        display_text=payload.message,
        language=payload.language or project.text_language,
        metadata_json={},
    )
    session.add(user_message)

    scenes = await video_scenes(session, project) if project.status == "COMPLETE" else []
    account_text, account_voice = await _default_languages(session, project, payload)
    preparation = await prepare_lesson(
        create_llm_provider(settings),
        message=payload.message,
        project_request=project.request,
        conversation=conversation,
        account_text_language=account_text,
        account_voice_language=account_voice,
        project_text_language=project.text_language,
        project_voice_language=project.voice_language,
        learner_context={},
        mode=project.mode,
        attached_papers=[paper.title for paper in await project_papers(session, project.id)],
        attached_sources=[source.title for source in await project_sources(session, project.id)],
        video_scenes=planner_scenes(scenes),
        video_versions=await video_history(session, project),
        input_language_hint=payload.language,
        video_status=_video_status(project.status),
    )
    canonical = (
        preparation.canonical_message
        if preparation.planned
        else await translator.to_english(payload.message)
    )
    user_message.canonical_text = canonical
    user_message.language = preparation.input_language
    user_message.metadata_json = {"lesson_preparation": preparation.model_dump(mode="json")}
    text_language, voice_language = preparation.text_language, preparation.voice_language

    # Links the learner gave are read into the chat's sources before anything
    # uses them. One that cannot be read is reported, never replaced by others.
    read: list[SourceDocument] = []
    for link in preparation.source_links:
        try:
            read.append(
                await ingest_link(
                    session,
                    settings,
                    LocalObjectStore(settings.object_storage_root),
                    project.id,
                    link,
                )
            )
        except LinkUnreadable as error:
            return await _save_assistant(
                session,
                translator,
                project,
                canonical=f"I couldn't read {error.url}: {error.reason}. I haven't used other "
                "sources in its place. You can attach the page as a PDF with the + button, or "
                "paste its text here, and I'll use that.",
                action="answer",
                language=text_language,
            )
    if preparation.action == "generate_video" and preparation.source_scope == "not_said":
        # Whether outside material may be added is the learner's call.
        given = [doc.title for doc in read] or [
            doc.title for doc in await project_sources(session, project.id)
        ]
        named = ", ".join(f'"{title}"' for title in given)
        this = "this source" if len(given) == 1 else "these sources"
        return await _save_assistant(
            session,
            translator,
            project,
            canonical=preparation.reply
            or f"I've read {named}. Should the video use only {this}, or may I also add "
            f'outside sources from the web to fill gaps? Reply "only {this}" or "add outside '
            "sources\", and I'll start the video.",
            action="answer",
            language=text_language,
        )

    if preparation.action in {"edit_video", "localize_video"}:
        if project.status != "COMPLETE":
            return await _save_assistant(
                session,
                translator,
                project,
                canonical=preparation.reply
                or "The lesson is still being produced. I can change it as soon as the "
                "video is ready.",
                action="wait",
            )
        base = preparation.target_video_version
        if base is not None:
            scenes = await _scenes_of_version(session, project, scenes, base)
            if preparation.action == "edit_video":
                # An edit keeps the language of the video being edited.
                project.text_language, project.voice_language = text_language, voice_language
        if preparation.action == "localize_video":
            decision = await _localize(session, translator, project, preparation, scenes)
        else:
            decision = await _edit(session, translator, project, preparation, scenes, canonical)
        return replace(decision, base_version=base) if decision.action != "answer" else decision

    if preparation.action == "generate_video":
        if project.status in {"CREATED", "COMPLETE"}:
            # A new lesson is always a full build with the planner's languages,
            # length and sources. In a finished chat it becomes the next video
            # version; the earlier video stays playable and editable.
            had_video = project.status == "COMPLETE"
            project.mode = "both"
            project.text_language, project.voice_language = text_language, voice_language
            project.request = preparation.canonical_request or project.request
            if preparation.duration_minutes:
                project.target_duration_minutes = preparation.duration_minutes
            return await _save_assistant(
                session,
                translator,
                project,
                canonical=preparation.reply
                or (
                    "I will make a new video for this. The earlier video stays available in "
                    "this chat. You can keep chatting while the scenes are generated and verified."
                    if had_video
                    else "I will turn this conversation into a narrated visual lesson. You can "
                    "keep chatting while the scenes are generated and verified."
                ),
                action="generate_video",
            )
        if project.status in {"GENERATING", "EDITING", "LOCALIZING"}:
            return await _save_assistant(
                session,
                translator,
                project,
                canonical=preparation.reply
                or "I am preparing your lesson. Ask a question now, or ask for a change "
                "once the video is ready.",
                action="wait",
            )

    if project.status != "COMPLETE":
        # Before a video exists, the chat's languages follow the learner's choice.
        project.text_language, project.voice_language = text_language, voice_language
    if preparation.action == "answer" and preparation.reply:
        # The planner chose to ask the learner something before acting.
        return await _save_assistant(
            session,
            translator,
            project,
            canonical=preparation.reply,
            action="answer",
            language=text_language,
        )
    answer, checks = await _tutor_answer(
        session, settings, project, payload, conversation, canonical, prior_messages
    )
    return await _save_assistant(
        session,
        translator,
        project,
        canonical=answer,
        action="answer",
        language=text_language,
        checks=checks,
    )


async def video_scenes(session: AsyncSession, project: Project) -> list[Scene]:
    """The scenes of the video this conversation shows, in playback order."""
    return list(
        (
            await session.scalars(
                select(Scene)
                .where(
                    Scene.project_id == project.id,
                    Scene.timeline_start.is_not(None),
                    Scene.timeline_end.is_not(None),
                )
                .order_by(Scene.timeline_start)
            )
        ).all()
    )


async def video_history(session: AsyncSession, project: Project) -> list[dict]:
    """Every video made in this conversation, with its language and what prompted it."""
    videos = (
        await session.scalars(
            select(ProjectArtifact)
            .where(ProjectArtifact.project_id == project.id, ProjectArtifact.kind == "video")
            .order_by(ProjectArtifact.version)
        )
    ).all()
    if not videos:
        return []
    asked = (
        await session.scalars(
            select(TutorMessage)
            .where(TutorMessage.project_id == project.id, TutorMessage.role == "user")
            .order_by(TutorMessage.created_at)
        )
    ).all()
    current = max(video.version for video in videos)
    history = []
    for video in videos:
        payload = video.payload or {}
        text, voice = payload.get("text_language"), payload.get("voice_language")
        prompt = [m.canonical_text for m in asked if _utc(m.created_at) <= _utc(video.created_at)]
        history.append(
            {
                "version": video.version,
                "current": video.version == current,
                "language": text
                if text == voice
                else (f"text {text}, voice {voice}" if text else None),
                "after": prompt[-1] if prompt else None,
            }
        )
    return history


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _scenes_of_version(
    session: AsyncSession, project: Project, scenes: list[Scene], version: int
) -> list:
    """The scenes with the timeline of an earlier video version (read-only copies)."""
    rows = (
        await session.scalars(
            select(TimelineSegment).where(
                TimelineSegment.project_id == project.id,
                TimelineSegment.video_version == version,
            )
        )
    ).all()
    spans = {row.scene_id: (row.start_seconds, row.end_seconds) for row in rows}
    copies = [
        SimpleNamespace(
            scene_key=scene.scene_key,
            title=scene.title,
            timeline_start=spans[scene.id][0],
            timeline_end=spans[scene.id][1],
        )
        for scene in scenes
        if scene.id in spans
    ]
    return sorted(copies, key=lambda item: item.timeline_start) or scenes


def planner_scenes(scenes: list[Scene]) -> list[dict]:
    """The video's scenes as the planner sees them."""
    return [
        {
            "id": scene.scene_key,
            "start": scene.timeline_start,
            "end": scene.timeline_end,
            "title": scene.title,
        }
        for scene in scenes
    ]


async def _default_languages(
    session: AsyncSession, project: Project, payload: TutorChatRequest
) -> tuple[str, str]:
    """The languages to use when the learner states no preference."""
    account = (
        await session.get(UserAccount, project.owner_id) if project.owner_id is not None else None
    )
    workspace = None
    if account is None:
        workspace = await session.scalar(
            select(WorkspaceProfile).where(WorkspaceProfile.profile_key == "local")
        )
    saved = account or workspace
    text = project.text_language or (saved.default_text_language if saved else "English")
    voice = project.voice_language or (saved.default_voice_language if saved else "English")
    return payload.text_language or text, payload.voice_language or voice


def _video_status(status: str) -> str:
    """The project's state in words the planner can reason about."""
    return {
        "COMPLETE": "a finished video is ready",
        "GENERATING": "a video is still being produced",
        "EDITING": "the video is being changed",
        "LOCALIZING": "the video's language is being changed",
        "FAILED": "the last video build failed",
    }.get(status, "no video yet")


def edit_range_for(preparation, scenes: list[Scene]) -> tuple[float, float] | None:
    """The time range a planner edit refers to, validated against the real video.

    Scenes or a time range the planner named come first. Otherwise the edit
    applies to the whole video: had the planner been unsure which part was
    meant, it would have asked rather than chosen edit_video.
    """
    if not scenes:
        return None
    chosen = [scene for scene in scenes if scene.scene_key in set(preparation.target_scenes)]
    if chosen and not preparation.whole_video:
        return min(s.timeline_start for s in chosen), max(s.timeline_end for s in chosen)
    start, end = preparation.edit_start_seconds, preparation.edit_end_seconds
    length = max(scene.timeline_end for scene in scenes)
    if not preparation.whole_video and start is not None and end is not None:
        # A named time range must lie inside the real video.
        if 0 <= start < end <= length + 1:
            return float(start), float(min(end, length))
        return None
    return min(scene.timeline_start for scene in scenes), length


async def _edit(session, translator, project, preparation, scenes, canonical) -> TutorDecision:
    span = edit_range_for(preparation, scenes)
    if span is None:
        # Only reached when the named time range lies outside the video.
        length = max((scene.timeline_end for scene in scenes), default=0.0)
        return await _save_assistant(
            session,
            translator,
            project,
            canonical=f"The video is {length:.0f} seconds long, so that time range is outside "
            "it. Which part should I change?",
            action="answer",
        )
    start, end = span
    numbers = [
        str(i) for i, s in enumerate(scenes, 1) if s.timeline_start < end and s.timeline_end > start
    ]
    label = f"scene {numbers[0]}" if len(numbers) == 1 else f"scenes {', '.join(numbers)}"
    confirmation = await _save_assistant(
        session,
        translator,
        project,
        canonical=preparation.reply
        or f"I will update {label} and rebuild only {'it' if len(numbers) == 1 else 'them'}.",
        action="edit_range",
        start=start,
        end=end,
    )
    return TutorDecision(
        confirmation.message,
        confirmation.canonical_message,
        "edit_range",
        start,
        end,
        preparation.edit_instruction or canonical,
    )


async def _localize(session, translator, project, preparation, scenes) -> TutorDecision:
    language = preparation.text_language
    chosen = (
        []
        if preparation.whole_video
        else [scene for scene in scenes if scene.scene_key in set(preparation.target_scenes)]
    )
    # The localize workflows read the project languages.
    project.text_language, project.voice_language = language, preparation.voice_language
    if chosen and len(chosen) < len(scenes):
        numbers = ", ".join(str(scenes.index(scene) + 1) for scene in chosen)
        label = f"scene{'s' if len(chosen) > 1 else ''} {numbers}"
        confirmation = await _save_assistant(
            session,
            translator,
            project,
            canonical=preparation.reply
            or f"I will explain {label} in {language} and keep the other scenes as they are.",
            action="localize_scene",
            start=min(scene.timeline_start for scene in chosen),
            end=max(scene.timeline_end for scene in chosen),
        )
        return TutorDecision(
            confirmation.message,
            confirmation.canonical_message,
            "localize_scene",
            confirmation.start,
            confirmation.end,
            ",".join(scene.scene_key for scene in chosen),
        )
    return await _save_assistant(
        session,
        translator,
        project,
        canonical=preparation.reply
        or "I will regenerate the visible lesson text and narration in the selected "
        "languages while preserving the English lesson plan and source grounding.",
        action="localize",
    )


async def _checked_reply(llm, settings, prompt: str, body: str, context: str) -> tuple[str, dict]:
    """Verify a reply's arithmetic and citations; regenerate once if they fail.

    Problems that remain after the retry are named in a short note on the
    reply, so the learner is never shown an unverified number as checked.
    """
    problems = answer_problems(body, context)
    if not problems:
        return body, {"verified": True}
    retry = (
        f"{prompt}\n\nYOUR PREVIOUS ANSWER:\n{body}\n\nIt has these verified errors:\n- "
        + "\n- ".join(problems[:6])
        + "\nRewrite the complete answer with them corrected. Recompute every number, and "
        "cite only pages that appear in the source excerpts."
    )
    try:
        second = (await llm.generate_text(retry, temperature=settings.main_llm_temperature)).strip()
    except Exception as error:  # noqa: BLE001 - keep the first answer with a note
        logger.warning("Reply correction failed: %s", error)
        second = ""
    remaining = answer_problems(second, context) if second else problems
    if second and len(remaining) < len(problems):
        body = second
    else:
        remaining = problems
    if remaining:
        body += "\n\n> **Note:** I could not verify " + "; ".join(remaining[:3]) + "."
    return body, {
        "verified": not remaining,
        "fixed": [problem for problem in problems if problem not in remaining],
        "unverified": remaining,
    }


async def _tutor_answer(
    session, settings, project, payload, conversation, canonical, prior_messages
) -> tuple[str, dict | None]:
    retrieval_query = " ".join(
        [message.canonical_text for message in prior_messages if message.role == "user"][-3:]
        + [canonical]
    )
    paper_context = await _paper_context(session, project, retrieval_query)
    if getattr(create_llm_provider(settings), "name", "") == "mock":
        return (
            f"For this lesson, focus on the relationship behind {project.request}. "
            "Tell me which moment was unclear, or which scene you want changed."
        ), None
    llm = create_llm_provider(settings)
    effort = payload.reasoning_effort or settings.tutor_reasoning_effort
    use_web = (
        settings.web_search_enabled and paper_context == NO_SOURCES and supports_web_search(llm)
    )
    prompt = (
        "You are a patient STEM tutor for a student who may phrase questions loosely. Answer "
        "in canonical English using the context below and clear Markdown. When the learner asks "
        "you to explain a concept, explain it fully: the intuition, the precise definition, "
        "each symbol in any equation, one small worked example with numbers, and a common "
        "misconception; end with a one-line takeaway. Keep narrow follow-ups and "
        "clarifications short. Write inline mathematics inside $...$ and display mathematics "
        "inside $$...$$ so the chat client can render it with KaTeX. Use the recent conversation to "
        "resolve references such as ‘that’, ‘the previous example’, or ‘your last answer’; do "
        "not repeat a generic answer when the learner asks a follow-up. If source excerpts are "
        "attached, ground claims in them, cite them as [source, p. N], and say when they do "
        "not support an answer."
        + (
            " Search the web for accurate, current information and rely on reputable sources."
            if use_web
            else ""
        )
        + f"\n\nLESSON REQUEST:\n{project.request}\n\nRECENT CONVERSATION (oldest first):\n"
        f"{conversation}\n\nSOURCE EXCERPTS:\n{paper_context}\n\n"
        f"CURRENT LEARNER MESSAGE:\n{canonical}"
    )
    sources: list[dict] = []
    try:
        if hasattr(llm, "generate_text_with_sources"):
            body, sources = await llm.generate_text_with_sources(
                prompt,
                temperature=settings.main_llm_temperature,
                reasoning_effort=effort,
                web_search=use_web,
                max_results=settings.web_search_max_results,
            )
        else:
            body = await llm.generate_text(prompt, temperature=settings.main_llm_temperature)
    except Exception as error:  # noqa: BLE001 - provider failures must not discard a chat turn
        logger.warning("Tutor model unavailable; using a bounded lesson fallback: %s", error)
        return _fallback_tutor_answer(project, canonical, paper_context).strip(), None
    context = "" if paper_context == NO_SOURCES else paper_context
    body, checks = await _checked_reply(llm, settings, prompt, body.strip(), context)
    return (body + format_sources(sources)).strip(), checks


async def respond_ephemeral(
    settings: Settings, payload: TutorChatRequest, history: list[str] | None = None
) -> str:
    """Answer a temporary chat without creating a project or message rows."""
    translator = TranslationService(
        create_translation_provider(settings), create_machine_translator(settings)
    )
    input_language = payload.language or "English"
    canonical = await translator.to_english(payload.message, source_language=input_language)
    context = "\n".join(history or [])
    if getattr(create_llm_provider(settings), "name", "") == "mock":
        answer = (
            f"Let’s explore {canonical.strip()}. Start with the core definition, "
            "then test it with a small example."
        )
    else:
        llm = create_llm_provider(settings)
        prompt = (
            "You are a patient STEM tutor. Answer the learner directly in clear Markdown. When "
            "asked to explain a concept, give the intuition, the precise definition, each symbol "
            "of any equation, a small worked example and a common misconception; keep follow-ups "
            "short. Use inline mathematics inside $...$ and display mathematics inside $$...$$. "
            "This is a temporary chat, so do not mention projects, saved lessons, or persistence.\n\n"
            f"RECENT TEMPORARY CHAT:\n{context}\n\nLEARNER MESSAGE:\n{canonical}"
        )
        sources: list[dict] = []
        if hasattr(llm, "generate_text_with_sources"):
            answer, sources = await llm.generate_text_with_sources(
                prompt,
                temperature=settings.main_llm_temperature,
                reasoning_effort=payload.reasoning_effort or settings.tutor_reasoning_effort,
                web_search=settings.web_search_enabled,
                max_results=settings.web_search_max_results,
            )
        else:
            answer = await llm.generate_text(prompt, temperature=settings.main_llm_temperature)
        answer, _ = await _checked_reply(llm, settings, prompt, answer.strip(), "")
        answer += format_sources(sources)
    return await translator.translate(
        answer.strip(),
        source_language="English",
        target_language=payload.text_language or input_language,
    )


async def _save_assistant(
    session: AsyncSession,
    translator: TranslationService,
    project: Project,
    *,
    canonical: str,
    action: str,
    start: float | None = None,
    end: float | None = None,
    language: str | None = None,
    checks: dict | None = None,
) -> TutorDecision:
    language = language or project.text_language
    display = await translator.translate(
        canonical, source_language="English", target_language=language
    )
    session.add(
        TutorMessage(
            project_id=project.id,
            role="assistant",
            canonical_text=canonical,
            display_text=display,
            language=language,
            action=action,
            metadata_json={"start": start, "end": end, **({"checks": checks} if checks else {})},
        )
    )
    return TutorDecision(display, canonical, action, start, end)
