"""Project build, edit, and localization services shared by the API and the worker."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import delete, func, or_, select
from temporalio import activity as temporal_activity

from scholarmotion.agents.feedback_classifier import classify_feedback
from scholarmotion.agents.lesson_preparation import LessonPreparation, prepare_lesson
from scholarmotion.agents.paper_outline import (
    PaperOutline,
    lecture_minutes,
    outline_paper,
    referenced_figures,
)
from scholarmotion.agents.script_writer import revise_narration
from scholarmotion.api.tutor import is_active
from scholarmotion.config import Settings
from scholarmotion.editing.dependencies import invalidation_for
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.media.alignment import align_words
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.media.subtitles import cues_to_srt, cues_to_vtt, merge_scene_cues
from scholarmotion.media.timeline import build_timeline, media_duration
from scholarmotion.memory.correction_memory import CorrectionMemory
from scholarmotion.observability.usage import (
    UsageLedger,
    current_ledger,
    track_usage,
    usage_stage,
)
from scholarmotion.persistence.database import Base, Database
from scholarmotion.persistence.models import (
    AudioArtifact,
    Chapter,
    CorrectionCandidate,
    CorrectionEntry,
    FeedbackEvent,
    GeneratedCode,
    ModelInvocation,
    ProgressEvent,
    Project,
    ProjectArtifact,
    ProjectRevision,
    RenderArtifact,
    Scene,
    SceneSpecification,
    SceneVersion,
    SourceChunk,
    SourceClaim,
    SourceDocument,
    TimelineSegment,
    TutorMessage,
    UserAccount,
    VerificationIssue,
    VerificationRun,
    WorkspaceProfile,
)
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantUnavailable, QdrantVectorStore
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_code_provider,
    create_embedding_provider,
    create_llm_provider,
    create_planner_provider,
    create_translation_provider,
    create_tts_provider,
    create_video_provider,
    create_vision_provider,
)
from scholarmotion.retrieval.hybrid_retrieval import (
    HybridRetriever,
    RetrievedChunk,
)
from scholarmotion.retrieval.paper_figures import extract_figures
from scholarmotion.retrieval.project_papers import (
    choose_paper,
    project_papers,
    project_sources,
)
from scholarmotion.retrieval.qdrant_retrieval import QdrantHybridRetriever
from scholarmotion.retrieval.web_research import web_research
from scholarmotion.schemas import ArtifactKind, PaperFigure, SceneSpec, TimingCue, TTSResult
from scholarmotion.services import BuildResult, ScholarMotionPipeline
from scholarmotion.skills import SkillService
from scholarmotion.translation import TranslationService

logger = logging.getLogger(__name__)


def _verification_chat_summary(result: BuildResult) -> tuple[str, dict]:
    """Create the learner-visible QA handoff for a completed build.

    Progress events are useful to the worker but were previously invisible in
    the tutor chat, leaving students unable to tell whether a video had merely
    rendered or had passed its quality gates. Keep the summary factual and
    compact so it remains useful in the normal conversation transcript.
    """
    duration_seconds = result.timeline.scenes[-1].end if result.timeline.scenes else 0.0
    reports = [scene.report for scene in result.scenes]
    layer_names = sorted(
        {name for report in reports for name, passed in report.layers.items() if passed}
    )
    issue_count = sum(len(report.issues) for report in reports)
    summary = (
        f"Video ready: {len(result.scenes)} scenes, {duration_seconds:.1f} seconds, and SRT/VTT "
        "subtitles were assembled. "
        f"Quality checks passed for {', '.join(layer_names) or 'the available render checks'}. "
        + (
            "No verification issues remain in the active scene reports."
            if issue_count == 0
            else f"{issue_count} non-blocking verification finding(s) are recorded for review."
        )
    )
    degraded = [scene.spec.scene_id for scene in result.scenes if not scene.report.passed]
    if degraded:
        summary += (
            f" Scene(s) {', '.join(degraded)} did not pass every check after all repairs; the "
            "best available version is shown and can be regenerated from the editor."
        )
    if result.blocked_scenes:
        missing = ", ".join(item["scene_id"] for item in result.blocked_scenes)
        summary += f" Scene(s) {missing} could not be produced and are left out of this video."
    review = result.lesson_review or {}
    problems = [str(problem) for problem in review.get("problems") or []]
    if problems:
        summary += " Whole-lesson review notes: " + "; ".join(problems[:3])
    elif review.get("coherent") and review.get("natural_ending"):
        summary += " The whole-lesson review found the scenes coherent with a natural ending."
    return summary, {
        "lesson_review": review,
        "scene_count": len(result.scenes),
        "duration_seconds": round(duration_seconds, 3),
        "subtitle_formats": ["srt", "vtt"],
        "passed_layers": layer_names,
        "issue_count": issue_count,
        "degraded_scenes": degraded,
        "missing_scenes": [item["scene_id"] for item in result.blocked_scenes],
    }


def _verification_failure_chat_summary(error: Exception) -> tuple[str, dict]:
    """Create an honest learner-facing QA handoff when publishing is blocked."""
    reason = str(error).strip() or "The quality gate did not approve the build."
    return (
        (
            "Video not published: the quality gate blocked this build, so no incomplete video was "
            f"shown. Reason: {reason}"
        ),
        {"published": False, "quality_gate": "blocked", "reason": reason},
    )


async def reset_failed_generation(session, project: Project) -> bool:
    """Clear derived rows before retrying a failed build on the same chat."""
    if project.status != "FAILED":
        return False

    scene_ids = select(Scene.id).where(Scene.project_id == project.id)
    # SQLite tests do not enable foreign-key cascades by default, so remove
    # scene-owned rows explicitly in dependency order before deleting scenes.
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != Scene.__tablename__ and "scene_id" in table.c:
            await session.execute(delete(table).where(table.c.scene_id.in_(scene_ids)))
    await session.execute(delete(Scene).where(Scene.project_id == project.id))
    await session.execute(delete(TimelineSegment).where(TimelineSegment.project_id == project.id))
    await session.execute(delete(Chapter).where(Chapter.project_id == project.id))
    await session.execute(
        delete(ProjectArtifact).where(
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.kind != "narration_profile",
        )
    )
    await session.execute(delete(SourceClaim).where(SourceClaim.project_id == project.id))
    project.active_video_path = None
    return True


async def retrieve_context(
    session,
    settings: Settings,
    embeddings,
    project: Project,
    chunk_values: list[dict],
    query: str | None = None,
) -> list[RetrievedChunk]:
    """Query Qdrant, then hydrate authoritative source records from PostgreSQL."""
    query = query or project.request
    try:
        return await QdrantHybridRetriever(
            embeddings, QdrantVectorStore(QdrantConfig.from_settings(settings))
        ).search(session, query, project_id=str(project.id), limit=20)
    except QdrantUnavailable as error:
        logger.warning("Qdrant retrieval unavailable, using in-process fallback: %s", error)
    if not chunk_values:
        return []
    return await HybridRetriever(embeddings).search(query, chunk_values, limit=20)


GIVEN_SOURCE_FULL_READ_CHUNKS = 60


async def given_source_chunks(embeddings, chunks: list[dict], query: str) -> list[dict]:
    """The learner's own sources for a lesson, in reading order.

    A short source (a blog post, an article) is used whole, so the lesson can
    follow its argument; a long one contributes its passages most relevant to
    the request, still in the order they appear.
    """
    if len(chunks) <= GIVEN_SOURCE_FULL_READ_CHUNKS:
        return list(chunks)
    position = {chunk["id"]: index for index, chunk in enumerate(chunks)}
    found = await HybridRetriever(embeddings).search(query, chunks, limit=40)
    selected = [{**item.chunk, "score": item.score} for item in found]
    return sorted(selected, key=lambda chunk: position.get(chunk["id"], 0))


def create_pipeline(
    settings: Settings,
    session,
    *,
    text_language: str = "English",
    voice_language: str = "English",
    progress_callback=None,
    narration_profile: dict | None = None,
) -> ScholarMotionPipeline:
    if narration_profile:
        settings = settings.model_copy(update=narration_profile)
    return ScholarMotionPipeline(
        llm=create_llm_provider(settings),
        code_llm=create_code_provider(settings),
        planner_llm=create_planner_provider(settings),
        vision=create_vision_provider(settings),
        video_verifier=create_video_provider(settings),
        aligner=(
            functools.partial(align_words, model_id=settings.narration_aligner_model)
            if settings.narration_alignment
            else None
        ),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
        # Never substitute text placeholders for production media.  A missing
        # renderer/assembler must surface as a failed build rather than a file
        # called `.mp4` that no player can open.
        renderer=SandboxRenderer(
            settings.manim_binary,
            settings.render_timeout_seconds,
            mock_missing=False,
            memory_limit_mb=settings.sandbox_memory_limit_mb,
            cpu_seconds=settings.sandbox_cpu_seconds,
            process_limit=settings.sandbox_process_limit,
            ffmpeg_binary=settings.ffmpeg_binary,
            isolation_mode=settings.sandbox_isolation,
        ),
        assembler=FFmpegAssembler(settings.ffmpeg_binary, mock_missing=False),
        correction_memory=CorrectionMemory(session),
        skill_service=SkillService(session),
        translator=TranslationService(create_translation_provider(settings)),
        text_language=text_language,
        voice_language=voice_language,
        max_concurrent_scenes=settings.max_concurrent_scenes,
        progress_callback=progress_callback,
        static_layout_gate=settings.static_layout_gate,
    )


async def _narration_profile(session, project_id: UUID, settings: Settings) -> dict:
    """Pin vocal identity across scenes, edits and language rebuilds; no credentials."""
    row = await session.scalar(
        select(ProjectArtifact).where(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.kind == "narration_profile",
            ProjectArtifact.version == 1,
        )
    )
    if row is not None:
        return row.payload
    fields = (
        "tts_provider",
        "tts_model",
        "tts_voice",
        "tts_base_url",
        "tts_instructions",
        "tts_response_format",
        "tts_pcm_sample_rate",
    )
    profile = {name: getattr(settings, name) for name in fields}
    session.add(
        ProjectArtifact(
            project_id=project_id,
            kind="narration_profile",
            version=1,
            uri=f"{project_id}/artifacts/narration_profile/v1.json",
            payload=profile,
        )
    )
    await session.flush()
    return profile


async def record_event(
    session,
    project_id: UUID,
    event: str,
    *,
    scene_id: UUID | None = None,
    detail: dict | None = None,
) -> None:
    session.add(
        ProgressEvent(project_id=project_id, scene_id=scene_id, event=event, detail=detail or {})
    )


async def _save_preparation_artifact(
    session, project_id: UUID, payload: dict, version: int = 1
) -> None:
    """Upsert preparation metadata so failed builds remain retryable."""
    artifact = await session.scalar(
        select(ProjectArtifact).where(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.kind == ArtifactKind.PREPARATION.value,
            ProjectArtifact.version == version,
        )
    )
    uri = f"{project_id}/artifacts/preparation/v{version}.json"
    if artifact is None:
        session.add(
            ProjectArtifact(
                project_id=project_id,
                kind=ArtifactKind.PREPARATION.value,
                version=version,
                uri=uri,
                payload=payload,
            )
        )
        return
    artifact.uri = uri
    artifact.payload = payload
    artifact.active = True
    artifact.stale = False


async def next_build_versions(session, project_id: UUID) -> tuple[int, int]:
    """(video version for a new lesson build, newest scene artifact version).

    A chat's second lesson becomes video v2, and its scene files are numbered
    above every existing scene version, so the earlier video stays playable
    and restorable instead of being overwritten.
    """
    video = await session.scalar(
        select(func.max(ProjectArtifact.version)).where(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.kind == ArtifactKind.VIDEO.value,
        )
    )
    base = 0
    for model in (SceneSpecification, GeneratedCode, AudioArtifact, RenderArtifact):
        newest = await session.scalar(
            select(func.max(model.version))
            .join(Scene, model.scene_id == Scene.id)
            .where(Scene.project_id == project_id)
        )
        base = max(base, newest or 0)
    return (video or 0) + 1, base


_PRICE_CACHE: dict[str, dict[str, float]] = {}


async def _openrouter_prices(settings: Settings) -> dict[str, dict[str, float]]:
    """Per-token prices from OpenRouter's public model list (cached per process)."""
    if _PRICE_CACHE:
        return _PRICE_CACHE
    base_url = (settings.main_llm_base_url or "").rstrip("/")
    if "openrouter.ai" not in base_url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{base_url}/models")
            response.raise_for_status()
        for model in response.json().get("data", []):
            pricing = model.get("pricing") or {}
            prices = {}
            for key in ("prompt", "completion", "input_cache_read"):
                try:
                    prices[key] = float(pricing[key])
                except (KeyError, TypeError, ValueError):
                    continue
            if prices:
                _PRICE_CACHE[model["id"]] = prices
    except Exception as error:  # noqa: BLE001 - pricing is informational only
        logger.info("Could not load OpenRouter prices: %s", error)
    return _PRICE_CACHE


async def _record_usage_summary(
    database: Database, settings: Settings, project_id: UUID, ledger: UsageLedger, operation: str
) -> None:
    """Persist what this operation's model calls cost, for the UI and audits."""
    if not ledger.entries:
        return
    try:
        if any(entry.cost_usd is None for entry in ledger.entries):
            ledger.estimate_missing_costs(await _openrouter_prices(settings))
        summary = {"operation": operation, **ledger.summary()}
        storage = LocalObjectStore(settings.object_storage_root)
        version = 1
        while storage.exists(f"{project_id}/artifacts/usage/{operation}-v{version}.json"):
            version += 1
        storage.put_bytes(
            f"{project_id}/artifacts/usage/{operation}-v{version}.json",
            json.dumps(summary, indent=2).encode(),
            overwrite=True,
        )
        async with database.sessions() as session:
            await record_event(session, project_id, "usage.summary", detail=summary)
            await session.commit()
        logger.info(
            "%s for %s cost $%.4f over %d model calls",
            operation,
            project_id,
            summary["cost_usd"],
            summary["calls"],
        )
    except Exception as error:  # noqa: BLE001 - accounting must never fail a build
        logger.warning("Could not record usage summary for %s: %s", project_id, error)


def _metered(operation: str):
    """Track every model call an operation makes and record its total cost."""

    def decorate(function):
        @functools.wraps(function)
        async def wrapper(
            database: Database, settings: Settings, project_id: UUID, *args, **kwargs
        ):
            if current_ledger() is not None:
                # Nested operation (localize -> edit): the outer one reports.
                return await function(database, settings, project_id, *args, **kwargs)
            with track_usage() as ledger:
                try:
                    return await function(database, settings, project_id, *args, **kwargs)
                finally:
                    await _record_usage_summary(database, settings, project_id, ledger, operation)

        return wrapper

    return decorate


def _build_progress_callback(database: Database, project_id: UUID):
    async def report(event: str, scene_key: str | None = None, extra: dict | None = None) -> None:
        detail = {**(extra or {}), **({"scene_key": scene_key} if scene_key else {})}
        async with database.sessions() as progress_session:
            await record_event(progress_session, project_id, event, detail=detail)
            await progress_session.commit()
        try:
            temporal_activity.heartbeat({"event": event, **detail})
        except RuntimeError:
            # run_generation is also called directly by unit/integration tests.
            pass

    return report


async def record_correction_evidence(
    session,
    runtime: ScholarMotionPipeline,
    *,
    category: str,
    trigger: str,
    recommended_fix: str,
    evidence: dict,
) -> None:
    candidate = await session.scalar(
        select(CorrectionCandidate)
        .where(
            CorrectionCandidate.category == category,
            CorrectionCandidate.trigger_conditions == trigger,
            CorrectionCandidate.status == "candidate",
        )
        .order_by(CorrectionCandidate.created_at.desc())
    )
    if candidate is None:
        session.add(
            CorrectionCandidate(
                category=category,
                trigger_conditions=trigger,
                recommended_fix=recommended_fix,
                evidence_count=1,
                confidence=0.75,
                evidence=[evidence],
            )
        )
        return
    candidate.evidence_count += 1
    candidate.confidence = min(0.98, candidate.confidence + 0.08)
    candidate.evidence = [*candidate.evidence, evidence]
    if candidate.evidence_count < 2 or candidate.confidence < 0.8:
        return
    sequence = (await session.scalar(select(func.count(CorrectionEntry.id))) or 0) + 1
    correction_key = f"CORR-LEARNED-{sequence:04d}"
    entry = CorrectionEntry(
        correction_key=correction_key,
        category=category,
        trigger_conditions=trigger,
        anti_pattern=f"Repeat the observed {category} failure.",
        required_behavior=f"Prevent {category} and preserve assigned safe regions.",
        recommended_fix=recommended_fix,
        evidence_count=candidate.evidence_count,
        confidence=candidate.confidence,
        tags=[category],
        applicable_model="any",
        validation_tests=["automatic_repair_verification"],
    )
    session.add(entry)
    candidate.status = "promoted"
    # CorrectionEntry is the authoritative database write. Qdrant indexing is
    # rebuildable and intentionally decoupled from this transaction.


async def _outline_lecture_paper(
    session,
    runtime: ScholarMotionPipeline,
    project: Project,
    paper: SourceDocument,
    chunks: list[dict],
    *,
    requested_minutes: float | None,
) -> PaperOutline:
    """Read the attached paper and plan its lecture; sets the project's length."""
    await record_event(
        session,
        project.id,
        "paper.reading",
        detail={"title": paper.title, "pages": (paper.metadata_json or {}).get("pages")},
    )
    await session.commit()
    saved = (
        runtime._read_json(f"{project.id}/artifacts/paper_outline/v1.json")
        if runtime.resumable(str(project.id), project.request)
        else None
    )
    if saved:
        # Retrying an unfinished lecture keeps its plan (and so its script).
        outline = PaperOutline.model_validate(saved).model_copy(update={"figure_crops": []})
    else:
        with usage_stage("paper_outline"):
            outline = await outline_paper(
                runtime.llm,
                title=paper.title,
                request=project.request,
                pdf_path=runtime.storage.local_path(paper.uri),
                chunks=chunks,
                target_minutes=requested_minutes,
            )
    minutes = lecture_minutes(outline, requested_minutes)
    project.target_duration_minutes = minutes
    # The planner read the typeset title; it beats text-extraction guesses.
    if outline.title.strip() and getattr(runtime.llm, "name", "") != "mock":
        paper.title = outline.title.strip()[:500]
    labels = referenced_figures(outline)
    if labels:
        try:
            crops = await asyncio.to_thread(
                extract_figures,
                runtime.storage.local_path(paper.uri),
                runtime.storage.local_path(f"{project.id}/paper_figures"),
                labels=labels,
            )
        except Exception as error:  # noqa: BLE001 - figures enrich a lecture; never block it
            logger.warning("Could not crop figures from %s: %s", paper.title, error)
            crops = []
        outline.figure_crops = [
            PaperFigure(
                label=crop.label,
                caption=crop.caption,
                filename=Path(crop.path).name,
                path=crop.path,
                width=crop.width,
                height=crop.height,
            )
            for crop in crops
        ]
    runtime._json(f"{project.id}/artifacts/paper_outline/v1.json", outline)
    await record_event(
        session,
        project.id,
        "paper.outlined",
        detail={
            "title": outline.title,
            "minutes": round(minutes, 1),
            "chapters": [chapter.title for chapter in outline.chapters],
            "equations": sum(len(chapter.equations) for chapter in outline.chapters),
            "plots": sum(len(chapter.plots) for chapter in outline.chapters),
            "figures": [crop.label for crop in outline.figure_crops],
            "figures_missing": sorted(labels - {crop.label for crop in outline.figure_crops}),
        },
    )
    await session.commit()
    return outline


@_metered("build")
async def run_generation(database: Database, settings: Settings, project_id: UUID) -> BuildResult:
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        project.status = "GENERATING"
        await record_event(session, project_id, "project.started")
        await session.commit()
        try:
            source_rows = (
                await session.execute(
                    select(SourceChunk, SourceDocument)
                    .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                    .where(
                        or_(
                            SourceDocument.project_id == project_id,
                            SourceDocument.project_id.is_(None),
                        )
                    )
                    # Reading order: chunks are stored in document order.
                    .order_by(SourceChunk.document_id, SourceChunk.page, SourceChunk.created_at)
                )
            ).all()
            chunk_values = [
                {
                    "id": str(chunk.id),
                    "document_id": str(document.id),
                    "document_title": document.title,
                    "document_kind": document.kind,
                    "class_level": chunk.class_level,
                    "subject": chunk.subject,
                    "chapter": chunk.chapter,
                    "section": chunk.section,
                    "page": chunk.page,
                    "content_type": chunk.content_type,
                    "text": chunk.text,
                    "concept_tags": chunk.concept_tags,
                }
                for chunk, document in source_rows
            ]
            retrieved_chunks: list[dict] = []
            runtime = create_pipeline(
                settings,
                session,
                narration_profile=await _narration_profile(session, project_id, settings),
                text_language=project.text_language,
                voice_language=project.voice_language,
                progress_callback=_build_progress_callback(database, project_id),
            )
            await record_event(session, project_id, "preparation.started")
            await session.commit()
            account = (
                await session.get(UserAccount, project.owner_id)
                if project.owner_id is not None
                else None
            )
            workspace = None
            if account is None:
                workspace = await session.scalar(
                    select(WorkspaceProfile).where(WorkspaceProfile.profile_key == "local")
                )
            # The project stores the account defaults selected at creation,
            # and may have since been customized for this conversation. Use
            # those resolved project preferences before falling back to the
            # current account profile.
            account_text_language = project.text_language or (
                account.default_text_language
                if account
                else workspace.default_text_language
                if workspace
                else "English"
            )
            account_voice_language = project.voice_language or (
                account.default_voice_language
                if account
                else workspace.default_voice_language
                if workspace
                else "English"
            )
            recent_messages = list(
                (
                    await session.scalars(
                        select(TutorMessage)
                        .where(TutorMessage.project_id == project_id)
                        .order_by(TutorMessage.created_at.desc())
                        .limit(48)
                    )
                ).all()
            )
            recent_messages = [m for m in recent_messages if is_active(m)][:16]
            recent_messages.reverse()
            latest_user = next(
                (message for message in reversed(recent_messages) if message.role == "user"), None
            )
            preparation_message = (
                latest_user.display_text
                if latest_user
                else project.original_request or project.request
            )
            preparation_conversation = "\n".join(
                f"{message.role.upper()}: {message.display_text}\nCANONICAL: {message.canonical_text}"
                for message in recent_messages
            )
            cached_preparation = (
                (latest_user.metadata_json or {}).get("lesson_preparation")
                if latest_user is not None
                else None
            )
            papers = await project_papers(session, project_id)
            given_sources = await project_sources(session, project_id)
            video_version, scene_version_base = await next_build_versions(session, project_id)
            if cached_preparation:
                preparation = LessonPreparation.model_validate(cached_preparation)
            else:
                preparation = await prepare_lesson(
                    getattr(runtime, "planner_llm", None) or runtime.llm,
                    message=preparation_message,
                    project_request=project.request,
                    conversation=preparation_conversation,
                    account_text_language=account_text_language,
                    account_voice_language=account_voice_language,
                    project_text_language=project.text_language,
                    project_voice_language=project.voice_language,
                    # Reserved contract for a versioned student persona, accessibility profile,
                    # and guardian controls when those account features are introduced.
                    learner_context={},
                    attached_papers=[paper.title for paper in papers],
                    attached_sources=[source.title for source in given_sources],
                )
            project.request = preparation.canonical_request or project.request
            # A length the learner asked for is the planner's reading of their words.
            if preparation.duration_minutes:
                project.target_duration_minutes = preparation.duration_minutes
            project.text_language = preparation.text_language
            project.voice_language = preparation.voice_language
            runtime.text_language = preparation.text_language
            runtime.voice_language = preparation.voice_language
            scope = preparation.source_scope
            # A whole-paper lecture reads its one paper end to end instead of
            # retrieving the top matches across sources.
            lecture_paper = (
                choose_paper(papers, preparation.paper)
                if preparation.lesson_scope == "whole_paper"
                else None
            )
            if lecture_paper is not None:
                retrieved_chunks = sorted(
                    (
                        chunk
                        for chunk in chunk_values
                        if chunk["document_id"] == str(lecture_paper.id)
                    ),
                    key=lambda chunk: chunk.get("page") or 0,
                )
            elif scope in {"only_given", "given_plus_web"}:
                # A lesson made from the learner's own material reads that
                # material, not the best matches across every library.
                given_ids = {str(source.id) for source in given_sources}
                retrieved_chunks = await given_source_chunks(
                    runtime.embeddings,
                    [chunk for chunk in chunk_values if chunk["document_id"] in given_ids],
                    preparation.retrieval_query or project.request,
                )
                if not retrieved_chunks:
                    raise RuntimeError(
                        "The sources given for this lesson have no readable text, so no video "
                        "was made from them."
                    )
            else:
                retrieved = await retrieve_context(
                    session,
                    settings,
                    runtime.embeddings,
                    project,
                    chunk_values,
                    query=preparation.retrieval_query or project.request,
                )
                retrieved_chunks = [{**item.chunk, "score": item.score} for item in retrieved]
            # The web is searched only where the learner allowed it: as a
            # supplement to their sources, or when nothing saved matched.
            search_web = (
                settings.web_search_enabled
                and lecture_paper is None
                and (scope == "given_plus_web" or (scope != "only_given" and not retrieved_chunks))
            )
            if search_web:
                await record_event(
                    session,
                    project_id,
                    "retrieval.web_started",
                    detail={
                        "reason": "Adding outside web sources to your sources, as you allowed."
                        if scope == "given_plus_web"
                        else "No saved or library source matched; searching the web."
                    },
                )
                await session.commit()
                retrieved_chunks = retrieved_chunks + await web_research(
                    runtime.llm,
                    preparation.retrieval_query or project.request,
                    reasoning_effort=settings.tutor_reasoning_effort,
                    max_results=settings.web_search_max_results,
                )
            source_labels = list(
                dict.fromkeys(
                    str(chunk.get("document_title") or chunk.get("document_kind") or "Saved source")
                    for chunk in retrieved_chunks
                )
            )
            preparation_payload = preparation.model_dump(mode="json")
            preparation_payload["selected_sources"] = source_labels
            runtime.storage.put_bytes(
                f"{project_id}/artifacts/preparation/v{video_version}.json",
                json.dumps(preparation_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                overwrite=True,
            )
            generation_message = await session.scalar(
                select(TutorMessage)
                .where(
                    TutorMessage.project_id == project_id,
                    TutorMessage.role == "assistant",
                    TutorMessage.action == "generate_video",
                )
                .order_by(TutorMessage.created_at.desc())
            )
            if generation_message is not None:
                message_metadata = dict(generation_message.metadata_json or {})
                message_metadata["selected_sources"] = source_labels
                from_web = any(chunk.get("document_kind") == "web" for chunk in retrieved_chunks)
                message_metadata["source_note"] = (
                    (
                        "Using only your sources: "
                        if scope == "only_given"
                        else "Using your sources plus web sources: "
                        if scope == "given_plus_web" and from_web
                        else "Using web sources: "
                        if from_web
                        else "Using saved source material: "
                    )
                    + ", ".join(source_labels)
                    if source_labels
                    else "I checked your saved sources and found no relevant match, so this lesson uses a general explanation."
                )
                generation_message.metadata_json = message_metadata
            await _save_preparation_artifact(
                session, project_id, preparation_payload, video_version
            )
            await record_event(
                session,
                project_id,
                "preparation.complete",
                detail={
                    "text_language": preparation.text_language,
                    "voice_language": preparation.voice_language,
                    "action": preparation.action,
                    "sources": source_labels,
                    "source_type": (
                        "given"
                        if scope == "only_given"
                        else "given+web"
                        if scope == "given_plus_web"
                        else "web"
                        if any(chunk.get("document_kind") == "web" for chunk in retrieved_chunks)
                        else "library"
                        if retrieved_chunks
                        else "none"
                    ),
                    "rationale": preparation.rationale,
                },
            )
            await session.commit()
            paper_outline = None
            if lecture_paper is not None:
                paper_outline = await _outline_lecture_paper(
                    session,
                    runtime,
                    project,
                    lecture_paper,
                    retrieved_chunks,
                    requested_minutes=preparation.duration_minutes,
                )
            result = await runtime.build(
                str(project_id),
                project.request,
                duration_minutes=project.target_duration_minutes,
                # Planning/script/code contracts are always canonical English.
                language="English",
                retrieved_chunks=retrieved_chunks,
                paper_outline=paper_outline,
                preparation=preparation,
                video_version=video_version,
                scene_version_base=scene_version_base,
            )
            # A later lesson in the same chat reuses chapter and scene records
            # by key and adds new artifact versions, so earlier videos remain.
            existing_chapters = {
                chapter.chapter_key: chapter
                for chapter in (
                    await session.scalars(select(Chapter).where(Chapter.project_id == project_id))
                ).all()
            }
            existing_scenes = {
                scene.scene_key: scene
                for scene in (
                    await session.scalars(select(Scene).where(Scene.project_id == project_id))
                ).all()
            }
            chapter_rows: dict[str, Chapter] = {}
            for spec in [item.spec for item in result.scenes]:
                if spec.chapter_id not in chapter_rows:
                    chapter = existing_chapters.get(spec.chapter_id)
                    if chapter is None:
                        chapter = Chapter(project_id=project_id, chapter_key=spec.chapter_id)
                        session.add(chapter)
                    chapter.title = spec.title
                    chapter.order_index = len(chapter_rows) + 1
                    await session.flush()
                    chapter_rows[spec.chapter_id] = chapter
            scene_rows: list[Scene] = []
            for kind in (
                ArtifactKind.PROFILE,
                ArtifactKind.RETRIEVAL,
                ArtifactKind.CURRICULUM,
                ArtifactKind.PEDAGOGY,
                ArtifactKind.SCRIPT,
                ArtifactKind.STORYBOARD,
            ):
                session.add(
                    ProjectArtifact(
                        project_id=project_id,
                        kind=kind.value,
                        version=video_version,
                        uri=f"{project_id}/artifacts/{kind.value}/v{video_version}.json",
                        payload={},
                    )
                )
            review_key = f"{project_id}/artifacts/script_review/v{video_version}.json"
            if runtime.storage.local_path(review_key).exists():
                session.add(
                    ProjectArtifact(
                        project_id=project_id,
                        kind="script_review",
                        version=video_version,
                        uri=review_key,
                        payload=json.loads(
                            runtime.storage.local_path(review_key).read_text(encoding="utf-8")
                        ),
                    )
                )
            timeline_duration = {
                segment.scene_id: segment.end - segment.start for segment in result.timeline.scenes
            }
            for order, produced in enumerate(result.scenes, 1):
                spec = produced.spec
                scene = existing_scenes.pop(spec.scene_id, None)
                if scene is None:
                    scene = Scene(project_id=project_id, scene_key=spec.scene_id)
                    session.add(scene)
                else:
                    await _retire_scene_artifacts(session, scene.id)
                scene.chapter_id = chapter_rows[spec.chapter_id].id
                scene.title = spec.title
                scene.order_index = order
                scene.status = "COMPLETE"
                scene.active_spec_version = spec.version
                scene.active_code_version = produced.code_version
                scene.active_audio_version = produced.audio_version
                scene.active_render_version = produced.render_version
                scene.duration = timeline_duration.get(
                    spec.scene_id, produced.audio.duration_seconds
                )
                scene.verification_state = "PASSED" if produced.report.passed else "DEGRADED"
                await session.flush()
                scene_rows.append(scene)
                base = f"{project_id}/scenes/{spec.scene_id}"
                scene_version = (
                    await session.scalar(
                        select(func.max(SceneVersion.version)).where(
                            SceneVersion.scene_id == scene.id
                        )
                    )
                    or 0
                ) + 1
                session.add_all(
                    [
                        SceneVersion(
                            scene_id=scene.id,
                            version=scene_version,
                            reason="initial" if scene_version == 1 else "new lesson",
                            snapshot={
                                "spec_version": spec.version,
                                "code_version": produced.code_version,
                                "audio_version": produced.audio_version,
                                "render_version": produced.render_version,
                            },
                        ),
                        SceneSpecification(
                            scene_id=scene.id,
                            version=spec.version,
                            payload=spec.model_dump(mode="json"),
                            uri=f"{base}/spec/v{spec.version}.json",
                        ),
                        GeneratedCode(
                            scene_id=scene.id,
                            version=produced.code_version,
                            spec_version=spec.version,
                            uri=str(Path(produced.code_path)),
                            scene_class=f"Scene{spec.scene_id}",
                        ),
                        AudioArtifact(
                            scene_id=scene.id,
                            version=produced.audio_version,
                            narration_version=spec.version,
                            uri=produced.audio.audio_path,
                            duration=produced.audio.duration_seconds,
                            timings=[cue.model_dump() for cue in produced.audio.sentence_timings],
                        ),
                        RenderArtifact(
                            scene_id=scene.id,
                            version=produced.render_version,
                            code_version=produced.code_version,
                            audio_version=produced.audio_version,
                            uri=produced.render_path,
                            metadata_json={},
                        ),
                        ModelInvocation(
                            project_id=project_id,
                            agent="scene_code_generator",
                            provider=settings.main_llm_provider,
                            model=settings.main_llm_model,
                            prompt_version="scene-code-v1",
                            input_artifact_ids=[f"{scene.scene_key}:spec:v{spec.version}"],
                            output_artifact_id=f"{scene.scene_key}:code:v{produced.code_version}",
                            temperature=settings.main_llm_temperature,
                            latency_ms=0,
                        ),
                    ]
                )
                verification = VerificationRun(
                    scene_id=scene.id,
                    render_version=produced.render_version,
                    passed=produced.report.passed,
                    score=produced.report.score,
                    layers=produced.report.layers,
                )
                session.add(verification)
                await session.flush()
                for issue in produced.report.issues:
                    session.add(
                        VerificationIssue(
                            run_id=verification.id,
                            scene_id=scene.id,
                            category=issue.category,
                            severity=issue.severity,
                            confidence=issue.confidence,
                            payload=issue.model_dump(mode="json"),
                        )
                    )
                    if issue.correction_memory_candidate:
                        await record_correction_evidence(
                            session,
                            runtime,
                            category=issue.category,
                            trigger=issue.description,
                            recommended_fix=issue.suggested_repair,
                            evidence={"scene_id": spec.scene_id, "source": "verifier"},
                        )
                await record_event(
                    session,
                    project_id,
                    "scene.complete",
                    scene_id=scene.id,
                    detail={"scene_id": spec.scene_id},
                )
            for segment, scene in zip(result.timeline.scenes, scene_rows):
                scene.timeline_start, scene.timeline_end = segment.start, segment.end
                session.add(
                    TimelineSegment(
                        project_id=project_id,
                        video_version=video_version,
                        scene_id=scene.id,
                        start_seconds=segment.start,
                        end_seconds=segment.end,
                        render_version=segment.render_version,
                    )
                )
            session.add_all(
                [
                    ProjectArtifact(
                        project_id=project_id,
                        kind=ArtifactKind.VIDEO.value,
                        version=video_version,
                        uri=result.video_path,
                        payload={
                            "subtitles_srt": result.srt_path,
                            "subtitles_vtt": result.vtt_path,
                            "text_language": project.text_language,
                            "voice_language": project.voice_language,
                        },
                    ),
                    ProjectArtifact(
                        project_id=project_id,
                        kind=ArtifactKind.TIMELINE.value,
                        version=video_version,
                        uri=f"{project_id}/video/v{video_version}/timeline.json",
                        payload=result.timeline.model_dump(mode="json"),
                    ),
                ]
            )
            for unused in existing_scenes.values():
                # Part of an earlier video only; it is restored with that version.
                unused.timeline_start = unused.timeline_end = None
            project.active_video_path = result.video_path
            project.status = "COMPLETE"
            verification_summary, verification_metadata = _verification_chat_summary(result)
            display_summary = verification_summary
            if project.text_language.casefold() != "english":
                try:
                    display_summary = await TranslationService(
                        create_translation_provider(settings)
                    ).translate(
                        verification_summary,
                        source_language="English",
                        target_language=project.text_language,
                    )
                except Exception as error:  # noqa: BLE001 - QA proof must not disappear on translation failure
                    logger.warning("Could not localize completion QA chat summary: %s", error)
            session.add(
                TutorMessage(
                    project_id=project_id,
                    role="assistant",
                    canonical_text=verification_summary,
                    display_text=display_summary,
                    language=project.text_language,
                    action="verification_complete",
                    metadata_json=verification_metadata,
                )
            )
            await record_event(session, project_id, "video.complete")
            await session.commit()
            return result
        except Exception as exc:
            # A failed flush leaves the AsyncSession transaction unusable. Roll it
            # back before persisting the terminal state, otherwise the project can
            # remain GENERATING forever and hide the actual build error.
            await session.rollback()
            project = await session.get(Project, project_id)
            if project is not None:
                earlier = await session.scalar(
                    select(func.max(ProjectArtifact.version)).where(
                        ProjectArtifact.project_id == project_id,
                        ProjectArtifact.kind == ArtifactKind.VIDEO.value,
                    )
                )
                # A failed later lesson leaves the chat's earlier video current.
                if earlier and await activate_video_version(session, project, earlier):
                    project.status = "COMPLETE"
                else:
                    project.status = "FAILED"
                failure_summary, failure_metadata = _verification_failure_chat_summary(exc)
                session.add(
                    TutorMessage(
                        project_id=project_id,
                        role="assistant",
                        canonical_text=failure_summary,
                        display_text=failure_summary,
                        language=project.text_language,
                        action="verification_failed",
                        metadata_json=failure_metadata,
                    )
                )
                await record_event(
                    session, project_id, "project.failed", detail={"error": str(exc)}
                )
                await session.commit()
            raise


@_metered("edit")
async def edit_range(
    database: Database,
    settings: Settings,
    project_id: UUID,
    start: float,
    end: float,
    instruction: str,
    *,
    localization_only: bool = False,
) -> list[str]:
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        scenes = list(
            (
                await session.scalars(
                    select(Scene)
                    .where(Scene.project_id == project_id, Scene.timeline_start.is_not(None))
                    .order_by(Scene.order_index)
                )
            ).all()
        )
        affected = [
            scene
            for scene in scenes
            if scene.timeline_start is not None
            and scene.timeline_end is not None
            and scene.timeline_start < end
            and scene.timeline_end > start
        ]
        if not affected:
            raise ValueError("selected range does not intersect any scene")
        classifications = await classify_feedback(create_planner_provider(settings), instruction)
        classification = classifications[0]
        plan = invalidation_for(classification.feedback_type, instruction)
        runtime = create_pipeline(
            settings,
            session,
            narration_profile=await _narration_profile(session, project_id, settings),
            text_language=project.text_language,
            voice_language=project.voice_language,
        )
        affected_keys: list[str] = []
        for scene in affected:
            affected_keys.append(scene.scene_key)
            spec_row = await session.scalar(
                select(SceneSpecification).where(
                    SceneSpecification.scene_id == scene.id, SceneSpecification.active.is_(True)
                )
            )
            if spec_row is None:
                raise RuntimeError(f"active SceneSpec missing for {scene.scene_key}")
            spec = SceneSpec.model_validate(spec_row.payload)
            narration_changed = plan.regenerate_audio or localization_only
            if not localization_only and plan.root in {
                ArtifactKind.PROFILE,
                ArtifactKind.PEDAGOGY,
                ArtifactKind.SCRIPT,
                ArtifactKind.STORYBOARD,
                ArtifactKind.SPEC,
            }:
                spec_row.active = False
                # Rewrite the narration to answer the request. Appending the
                # instruction instead would make the speech engine read the
                # request itself aloud to the student.
                revised_narration = spec.narration
                if narration_changed:
                    revised_narration = await revise_narration(
                        runtime.llm,
                        spec.narration,
                        instruction,
                        learning_objective=spec.learning_objective or "",
                    )
                spec = spec.model_copy(
                    update={
                        "version": spec.version + 1,
                        "narration": revised_narration,
                        "tags": sorted(set(spec.tags + ["user_edit"])),
                    }
                )
                persist_spec = True
            else:
                persist_spec = False
            artifact_version = (
                max(
                    scene.active_code_version,
                    scene.active_audio_version,
                    scene.active_render_version,
                )
                + 1
            )
            existing_audio = None
            if not narration_changed:
                audio_row = await session.scalar(
                    select(AudioArtifact).where(
                        AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                    )
                )
                if audio_row:
                    existing_audio = TTSResult(
                        audio_path=audio_row.uri,
                        duration_seconds=audio_row.duration,
                        sentence_timings=audio_row.timings,
                        word_timings=[],
                    )
            produced = await runtime.produce_scene(
                str(project_id),
                spec,
                artifact_version=artifact_version,
                persist_spec=persist_spec,
                existing_audio=existing_audio,
                # A scene edit preserves its original grounded source set. If
                # these IDs are omitted, verification treats every citation as
                # unknown and blocks otherwise valid regeneration attempts.
                known_source_ids=set(spec.source_ids),
            )
            old_code = await session.scalar(
                select(GeneratedCode).where(
                    GeneratedCode.scene_id == scene.id, GeneratedCode.active.is_(True)
                )
            )
            old_render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id, RenderArtifact.active.is_(True)
                )
            )
            if old_code:
                old_code.active, old_code.stale = False, True
            if old_render:
                old_render.active, old_render.stale = False, True
            if narration_changed:
                old_audio = await session.scalar(
                    select(AudioArtifact).where(
                        AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                    )
                )
                if old_audio:
                    old_audio.active, old_audio.stale = False, True
                session.add(
                    AudioArtifact(
                        scene_id=scene.id,
                        version=artifact_version,
                        narration_version=spec.version,
                        uri=produced.audio.audio_path,
                        duration=produced.audio.duration_seconds,
                        timings=[cue.model_dump() for cue in produced.audio.sentence_timings],
                    )
                )
                scene.active_audio_version = artifact_version
                scene.duration = media_duration(
                    produced.audio.duration_seconds, produced.video_duration_seconds
                )
            if persist_spec:
                session.add(
                    SceneSpecification(
                        scene_id=scene.id,
                        version=spec.version,
                        payload=spec.model_dump(mode="json"),
                        uri=f"{project_id}/scenes/{scene.scene_key}/spec/v{spec.version}.json",
                    )
                )
                scene.active_spec_version = spec.version
            session.add(
                GeneratedCode(
                    scene_id=scene.id,
                    version=artifact_version,
                    spec_version=spec.version,
                    uri=produced.code_path,
                    scene_class=f"Scene{scene.scene_key}",
                )
            )
            session.add(
                RenderArtifact(
                    scene_id=scene.id,
                    version=produced.render_version,
                    code_version=artifact_version,
                    audio_version=scene.active_audio_version,
                    uri=produced.render_path,
                    metadata_json={"edit": instruction},
                )
            )
            scene.active_code_version = artifact_version
            scene.active_render_version = produced.render_version
            scene.status = "COMPLETE"
            for previous in (
                await session.scalars(
                    select(SceneVersion).where(
                        SceneVersion.scene_id == scene.id, SceneVersion.active.is_(True)
                    )
                )
            ).all():
                previous.active = False
            next_version = (
                await session.scalar(
                    select(func.max(SceneVersion.version)).where(SceneVersion.scene_id == scene.id)
                )
                or 0
            ) + 1
            session.add(
                SceneVersion(
                    scene_id=scene.id,
                    version=next_version,
                    reason=instruction,
                    snapshot={
                        "spec_version": scene.active_spec_version,
                        "code_version": scene.active_code_version,
                        "audio_version": scene.active_audio_version,
                        "render_version": scene.active_render_version,
                    },
                )
            )
            for result in classifications:
                session.add(
                    FeedbackEvent(
                        project_id=project_id,
                        scene_ids=[scene.scene_key],
                        feedback_type=result.feedback_type.value,
                        category=result.category,
                        instruction=instruction,
                        range_start=start,
                        range_end=end,
                    )
                )
                if result.memory_eligible:
                    await record_correction_evidence(
                        session,
                        runtime,
                        category=result.category or "feedback",
                        trigger=instruction,
                        recommended_fix=f"Prevent {result.category} for matching scene tags.",
                        evidence={"scene_id": scene.scene_key, "source": "user_feedback"},
                    )
        await session.flush()
        render_paths: list[str] = []
        audio_paths: list[str] = []
        scene_cues: list[tuple[float, list[TimingCue]]] = []
        cue_offset = 0.0
        for scene in scenes:
            render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id, RenderArtifact.active.is_(True)
                )
            )
            audio = await session.scalar(
                select(AudioArtifact).where(
                    AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                )
            )
            if render is None or audio is None:
                raise RuntimeError(f"active media missing for {scene.scene_key}")
            scene.duration = media_duration(
                audio.duration, runtime.assembler.probe_duration(render.uri)
            )
            render_paths.append(render.uri)
            audio_paths.append(audio.uri)
            scene_cues.append(
                (cue_offset, [TimingCue.model_validate(cue) for cue in audio.timings])
            )
            cue_offset += scene.duration
        video_version = (
            await session.scalar(
                select(func.max(ProjectArtifact.version)).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind == ArtifactKind.VIDEO.value,
                )
            )
            or 0
        ) + 1
        output_path = runtime.storage.local_path(f"{project_id}/video/v{video_version}/final.mp4")
        assembly = runtime.assembler.assemble(render_paths, output_path, audio_paths=audio_paths)
        if not assembly.success:
            raise RuntimeError(assembly.log[-1000:])
        timeline = build_timeline(
            [
                (scene.scene_key, scene.duration, scene.active_render_version, path)
                for scene, path in zip(scenes, render_paths)
            ]
        )
        runtime._json(f"{project_id}/video/v{video_version}/timeline.json", timeline)
        cues = merge_scene_cues(scene_cues)
        srt_key = f"{project_id}/video/v{video_version}/subtitles.srt"
        vtt_key = f"{project_id}/video/v{video_version}/subtitles.vtt"
        runtime.storage.put_bytes(srt_key, cues_to_srt(cues).encode())
        runtime.storage.put_bytes(vtt_key, cues_to_vtt(cues).encode())
        for old in (
            await session.scalars(
                select(ProjectArtifact).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind.in_(
                        [ArtifactKind.VIDEO.value, ArtifactKind.TIMELINE.value]
                    ),
                    ProjectArtifact.active.is_(True),
                )
            )
        ).all():
            old.active = False
        session.add_all(
            [
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.VIDEO.value,
                    version=video_version,
                    uri=str(output_path),
                    payload={
                        "assembly_inputs": assembly.ordered_inputs,
                        "subtitles_srt": str(runtime.storage.local_path(srt_key)),
                        "subtitles_vtt": str(runtime.storage.local_path(vtt_key)),
                        "text_language": project.text_language,
                        "voice_language": project.voice_language,
                    },
                ),
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.TIMELINE.value,
                    version=video_version,
                    uri=f"{project_id}/video/v{video_version}/timeline.json",
                    payload=timeline.model_dump(mode="json"),
                ),
            ]
        )
        for segment, scene in zip(timeline.scenes, scenes):
            scene.timeline_start, scene.timeline_end = segment.start, segment.end
            session.add(
                TimelineSegment(
                    project_id=project_id,
                    video_version=video_version,
                    scene_id=scene.id,
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    render_version=scene.active_render_version,
                )
            )
        project.active_video_path = str(output_path)
        project.status = "COMPLETE"
        for revision in (
            await session.scalars(
                select(ProjectRevision).where(
                    ProjectRevision.project_id == project_id, ProjectRevision.active.is_(True)
                )
            )
        ).all():
            revision.active = False
        project.active_revision += 1
        session.add(
            ProjectRevision(
                project_id=project_id,
                version=project.active_revision,
                reason="timestamp_edit",
                payload={
                    "instruction": instruction,
                    "affected_scenes": affected_keys,
                    "video_version": video_version,
                },
                active=True,
            )
        )
        await record_event(
            session, project_id, "video.complete", detail={"selectively_regenerated": affected_keys}
        )
        await session.commit()
        return affected_keys


@_metered("localize")
async def localize_project(
    database: Database,
    settings: Settings,
    project_id: UUID,
    *,
    scene_key: str | None = None,
) -> list[str]:
    """Regenerate localized code/audio from immutable English scene specs."""
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        all_scenes = (
            await session.scalars(
                select(Scene)
                .where(Scene.project_id == project_id, Scene.timeline_start.is_not(None))
                .order_by(Scene.order_index)
            )
        ).all()
        scenes = (
            [scene for scene in all_scenes if scene.scene_key == scene_key]
            if scene_key
            else all_scenes
        )
        if scene_key and not scenes:
            raise ValueError(f"scene {scene_key} does not exist in this project")
        if not scenes or any(
            scene.timeline_start is None or scene.timeline_end is None for scene in scenes
        ):
            raise ValueError("complete the initial lesson before localizing it")
        start = min(scene.timeline_start for scene in scenes if scene.timeline_start is not None)
        end = max(scene.timeline_end for scene in scenes if scene.timeline_end is not None)
        project.status = "LOCALIZING"
        await record_event(
            session,
            project_id,
            "localization.started",
            detail={
                "text_language": project.text_language,
                "voice_language": project.voice_language,
                "scene_key": scene_key,
            },
        )
        await session.commit()
    try:
        affected = await edit_range(
            database,
            settings,
            project_id,
            start,
            end,
            "Regenerate localized presentation from the canonical English lesson.",
            localization_only=True,
        )
    except Exception:
        async with database.sessions() as session:
            project = await session.get(Project, project_id)
            if project is not None:
                project.status = "FAILED"
                await record_event(session, project_id, "localization.failed")
                await session.commit()
        raise
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is not None:
            project.status = "COMPLETE"
            await record_event(
                session, project_id, "localization.complete", detail={"scenes": affected}
            )
            await session.commit()
    return affected


async def reassemble_active(database: Database, settings: Settings, project_id: UUID) -> int:
    """Assemble a new immutable video version from the selected scene versions."""
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        scenes = list(
            (
                await session.scalars(
                    select(Scene)
                    .where(Scene.project_id == project_id, Scene.timeline_start.is_not(None))
                    .order_by(Scene.order_index)
                )
            ).all()
        )
        render_paths: list[str] = []
        audio_paths: list[str] = []
        scene_cues: list[tuple[float, list]] = []
        cursor = 0.0
        runtime = create_pipeline(settings, session)
        for scene in scenes:
            render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id,
                    RenderArtifact.version == scene.active_render_version,
                )
            )
            audio = await session.scalar(
                select(AudioArtifact).where(
                    AudioArtifact.scene_id == scene.id,
                    AudioArtifact.version == scene.active_audio_version,
                )
            )
            if render is None or audio is None:
                raise RuntimeError(f"active media missing for {scene.scene_key}")
            scene.duration = media_duration(
                audio.duration, runtime.assembler.probe_duration(render.uri)
            )
            render_paths.append(render.uri)
            audio_paths.append(audio.uri)
            scene_cues.append((cursor, [TimingCue.model_validate(cue) for cue in audio.timings]))
            cursor += scene.duration
        video_version = (
            await session.scalar(
                select(func.max(ProjectArtifact.version)).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind == ArtifactKind.VIDEO.value,
                )
            )
            or 0
        ) + 1
        output_path = runtime.storage.local_path(f"{project_id}/video/v{video_version}/final.mp4")
        assembly = runtime.assembler.assemble(render_paths, output_path, audio_paths=audio_paths)
        if not assembly.success:
            raise RuntimeError(assembly.log[-1000:])
        timeline = build_timeline(
            [
                (scene.scene_key, scene.duration, scene.active_render_version, path)
                for scene, path in zip(scenes, render_paths)
            ]
        )
        runtime._json(f"{project_id}/video/v{video_version}/timeline.json", timeline)
        cues = merge_scene_cues(scene_cues)
        srt_key = f"{project_id}/video/v{video_version}/subtitles.srt"
        vtt_key = f"{project_id}/video/v{video_version}/subtitles.vtt"
        runtime.storage.put_bytes(srt_key, cues_to_srt(cues).encode())
        runtime.storage.put_bytes(vtt_key, cues_to_vtt(cues).encode())
        for old in (
            await session.scalars(
                select(ProjectArtifact).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind.in_(
                        [ArtifactKind.VIDEO.value, ArtifactKind.TIMELINE.value]
                    ),
                    ProjectArtifact.active.is_(True),
                )
            )
        ).all():
            old.active = False
        session.add_all(
            [
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.VIDEO.value,
                    version=video_version,
                    uri=str(output_path),
                    payload={
                        "assembly_inputs": assembly.ordered_inputs,
                        "subtitles_srt": str(runtime.storage.local_path(srt_key)),
                        "subtitles_vtt": str(runtime.storage.local_path(vtt_key)),
                        "text_language": project.text_language,
                        "voice_language": project.voice_language,
                    },
                ),
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.TIMELINE.value,
                    version=video_version,
                    uri=f"{project_id}/video/v{video_version}/timeline.json",
                    payload=timeline.model_dump(mode="json"),
                ),
            ]
        )
        for segment, scene in zip(timeline.scenes, scenes):
            scene.timeline_start, scene.timeline_end = segment.start, segment.end
            session.add(
                TimelineSegment(
                    project_id=project_id,
                    video_version=video_version,
                    scene_id=scene.id,
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    render_version=scene.active_render_version,
                )
            )
        project.active_video_path = str(output_path)
        project.status = "COMPLETE"
        await record_event(session, project_id, "video.complete", detail={"restored_version": True})
        await session.commit()
        return video_version


async def _retire_scene_artifacts(session, scene_id: UUID) -> None:
    """Deactivate a scene's current artifacts before a new lesson replaces them."""
    for model in (SceneSpecification, GeneratedCode, AudioArtifact, RenderArtifact):
        for artifact in (
            await session.scalars(select(model).where(model.scene_id == scene_id))
        ).all():
            artifact.active, artifact.stale = False, True


async def activate_video_version(session, project: Project, version: int) -> bool:
    """Make an earlier video version's scenes the active ones, so an edit builds on it.

    Each timeline row records the render a scene used in that version, and each
    render records its code and audio, so the whole scene state is restored.
    The video's own languages are restored too when they were recorded.
    """
    segments = (
        await session.scalars(
            select(TimelineSegment).where(
                TimelineSegment.project_id == project.id,
                TimelineSegment.video_version == version,
            )
        )
    ).all()
    if not segments:
        return False
    for segment in segments:
        scene = await session.get(Scene, segment.scene_id)
        render = await session.scalar(
            select(RenderArtifact).where(
                RenderArtifact.scene_id == segment.scene_id,
                RenderArtifact.version == segment.render_version,
            )
        )
        if scene is None or render is None:
            return False
        code = await session.scalar(
            select(GeneratedCode).where(
                GeneratedCode.scene_id == scene.id, GeneratedCode.version == render.code_version
            )
        )
        chosen = {
            SceneSpecification: code.spec_version if code else scene.active_spec_version,
            GeneratedCode: render.code_version,
            AudioArtifact: render.audio_version or scene.active_audio_version,
            RenderArtifact: render.version,
        }
        for model, active_version in chosen.items():
            for artifact in (
                await session.scalars(select(model).where(model.scene_id == scene.id))
            ).all():
                artifact.active = artifact.version == active_version
                artifact.stale = artifact.version != active_version
        scene.active_spec_version = chosen[SceneSpecification]
        scene.active_code_version = chosen[GeneratedCode]
        scene.active_audio_version = chosen[AudioArtifact]
        scene.active_render_version = chosen[RenderArtifact]
        scene.timeline_start, scene.timeline_end = segment.start_seconds, segment.end_seconds
    # Scenes that version did not have are not part of the restored video.
    in_version = {segment.scene_id for segment in segments}
    for scene in (await session.scalars(select(Scene).where(Scene.project_id == project.id))).all():
        if scene.id not in in_version:
            scene.timeline_start = scene.timeline_end = None
    video = await session.scalar(
        select(ProjectArtifact).where(
            ProjectArtifact.project_id == project.id,
            ProjectArtifact.kind == ArtifactKind.VIDEO.value,
            ProjectArtifact.version == version,
        )
    )
    payload = (video.payload or {}) if video else {}
    if payload.get("text_language"):
        project.text_language = payload["text_language"]
    if payload.get("voice_language"):
        project.voice_language = payload["voice_language"]
    if video is not None:
        project.active_video_path = video.uri
    await session.flush()
    return True
