"""Learning API: public video catalog, library, notes, playlists, home, study plans, quizzes.

A "video" here is a project with a finished video. Its owner can always open
it; anyone can open it once the owner publishes it. Catalog details (title,
summary, subject, tags) are written by the catalog agent; recommendations and
related videos rank by embedding similarity, so nothing is keyword-ruled.
"""

import asyncio
import hashlib
import math
import re
import subprocess
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.agents.quiz_writer import QuizUnavailable, grade_written_answers, write_quiz
from scholarmotion.agents.study_planner import StudyPlanUnavailable, plan_study
from scholarmotion.agents.video_catalog import describe_video
from scholarmotion.api.service import openrouter_prices
from scholarmotion.config import Settings
from scholarmotion.observability import get_logger
from scholarmotion.observability.usage import track_usage
from scholarmotion.persistence.models import (
    Playlist,
    PlaylistItem,
    Project,
    Quiz,
    QuizAttempt,
    SavedVideo,
    Scene,
    SourceDocument,
    StudyPlan,
    StudyTask,
    TimelineSegment,
    UserAccount,
    VideoNote,
    WatchProgress,
    update_quietly,
)
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import create_llm_provider
from scholarmotion.services.embeddings import embed_texts
from scholarmotion.services.usage_records import new_run_id, usage_rows

logger = get_logger(__name__)

# Path prefixes this module serves; the API middleware authenticates them.
REQUIRED_AUTH_PREFIXES = (
    "/v1/library",
    "/v1/notes",
    "/v1/home",
    "/v1/study-plans",
    "/v1/quizzes",
)
OPTIONAL_AUTH_PREFIXES = ("/v1/videos",)
MEDIA_SUFFIXES = ("/video", "/chapters.vtt", "/subtitles.vtt", "/thumbnail.jpg", "/file")


# ---------------------------------------------------------------- request bodies


class CatalogUpdate(BaseModel):
    visibility: str | None = Field(default=None, pattern="^(public|private)$")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    subject: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=40)
    level: str | None = Field(default=None, max_length=80)


class ProgressUpdate(BaseModel):
    position_seconds: float = Field(ge=0)
    duration_seconds: float = Field(default=0, ge=0)


class NoteCreate(BaseModel):
    at_seconds: float = Field(default=0, ge=0)
    text: str = Field(min_length=1, max_length=10_000)


class NoteUpdate(BaseModel):
    at_seconds: float | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=10_000)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class PlaylistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class PlaylistItemAdd(BaseModel):
    video_id: UUID


class StudyPlanCreate(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    end_date: date | None = None
    start_date: date | None = None
    minutes_per_day: int = Field(default=30, ge=5, le=600)
    language: str | None = None


class StudyPlanUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: str | None = Field(default=None, pattern="^(active|archived|completed)$")


class ReplanRequest(BaseModel):
    instruction: str = Field(default="", max_length=4000)


class TaskUpdate(BaseModel):
    done: bool | None = None
    due_date: date | None = None


class QuizCreate(BaseModel):
    video_id: UUID | None = None
    topic: str | None = Field(default=None, max_length=2000)
    instruction: str = Field(default="", max_length=2000)
    language: str | None = None


class QuizSubmission(BaseModel):
    answers: list[int | str | None] = Field(max_length=15)


# ---------------------------------------------------------------- pure helpers


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _vtt_seconds(stamp: str) -> float:
    parts = stamp.strip().replace(",", ".").split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(text: str) -> list[dict[str, Any]]:
    """Cues of a WebVTT file as ``{start, end, text}``."""
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line for line in block.splitlines() if line.strip()]
        timing = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, _, rest = lines[timing].partition("-->")
        end = rest.strip().split(" ")[0]
        body = " ".join(re.sub(r"<[^>]+>", "", line) for line in lines[timing + 1 :]).strip()
        if body:
            cues.append({"start": _vtt_seconds(start), "end": _vtt_seconds(end), "text": body})
    return cues


def catalog_text(project: Project, scene_titles: list[str] | None = None) -> str:
    """What a video is about, as one text for embeddings and keyword search."""
    return " \n".join(
        part
        for part in (
            project.title,
            project.description or "",
            project.subject or "",
            " ".join(project.tags or []),
            project.level or "",
            project.original_request or "",
            project.request or "",
            " ".join(scene_titles or []),
        )
        if part
    )


def keyword_match(haystack: str, query: str) -> bool:
    """Every word of the query appears somewhere in the video's details."""
    text = haystack.casefold()
    return all(term in text for term in query.casefold().split())


# ---------------------------------------------------------------- the router


def create_learning_router(
    settings: Settings,
    store: LocalObjectStore,
    session_dependency: Callable,
    current_user_id: ContextVar[UUID | None],
) -> APIRouter:
    router = APIRouter(tags=["learning"])
    Session = Annotated[AsyncSession, Depends(session_dependency)]
    interest_cache: dict[str, list[float]] = {}

    async def embed(texts: list[str]) -> list[list[float]] | None:
        return await embed_texts(settings, texts)

    async def embed_cached(text: str) -> list[float] | None:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in interest_cache:
            vectors = await embed([text])
            if not vectors:
                return None
            interest_cache[key] = vectors[0]
        return interest_cache[key]

    def viewer() -> UUID | None:
        return current_user_id.get()

    def require_user() -> UUID:
        user_id = current_user_id.get()
        if user_id is None:
            raise HTTPException(401, "authentication required")
        return user_id

    async def save_usage(session: AsyncSession, ledger, project_id: UUID | None, operation: str):
        if not ledger.entries:
            return
        try:
            if any(entry.cost_usd is None for entry in ledger.entries):
                ledger.estimate_missing_costs(await openrouter_prices(settings))
            session.add_all(
                usage_rows(
                    ledger,
                    user_id=current_user_id.get(),
                    project_id=project_id,
                    operation=operation,
                    run_id=new_run_id(),
                )
            )
        except Exception as error:  # noqa: BLE001 - accounting must never fail a request
            logger.warning("Could not record %s usage: %s", operation, error)

    # ------------------------------------------------------------ video lookups

    async def open_video(session: AsyncSession, video_id: UUID) -> Project:
        """A finished video the viewer may watch: their own, or a published one."""
        project = await session.get(Project, video_id)
        if (
            project is None
            or not project.active_video_path
            or (project.visibility != "public" and project.owner_id != viewer())
        ):
            raise HTTPException(404, "video not found")
        return project

    async def owned_video(session: AsyncSession, video_id: UUID) -> Project:
        project = await session.get(Project, video_id)
        if project is None or project.owner_id != require_user():
            raise HTTPException(404, "video not found")
        if not project.active_video_path:
            raise HTTPException(409, "this chat has no finished video yet")
        return project

    async def durations(session: AsyncSession, ids: list[UUID]) -> dict[UUID, float]:
        if not ids:
            return {}
        rows = await session.execute(
            select(Scene.project_id, func.max(Scene.timeline_end))
            .where(Scene.project_id.in_(ids), Scene.timeline_end.is_not(None))
            .group_by(Scene.project_id)
        )
        return {project_id: float(end or 0) for project_id, end in rows.all()}

    async def scene_titles(session: AsyncSession, ids: list[UUID]) -> dict[UUID, list[str]]:
        if not ids:
            return {}
        rows = await session.execute(
            select(Scene.project_id, Scene.title)
            .where(Scene.project_id.in_(ids), Scene.timeline_start.is_not(None))
            .order_by(Scene.project_id, Scene.timeline_start)
        )
        titles: dict[UUID, list[str]] = {}
        for project_id, title in rows.all():
            titles.setdefault(project_id, []).append(title)
        return titles

    async def authors(session: AsyncSession, projects: list[Project]) -> dict[UUID, str]:
        ids = {item.owner_id for item in projects if item.owner_id}
        if not ids:
            return {}
        rows = await session.execute(
            select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(ids))
        )
        return dict(rows.all())

    async def progress_map(session: AsyncSession, ids: list[UUID]) -> dict[UUID, WatchProgress]:
        user_id = viewer()
        if user_id is None or not ids:
            return {}
        rows = await session.scalars(
            select(WatchProgress).where(
                WatchProgress.user_id == user_id, WatchProgress.project_id.in_(ids)
            )
        )
        return {item.project_id: item for item in rows.all()}

    async def saved_ids(session: AsyncSession, ids: list[UUID]) -> set[UUID]:
        user_id = viewer()
        if user_id is None or not ids:
            return set()
        rows = await session.scalars(
            select(SavedVideo.project_id).where(
                SavedVideo.user_id == user_id, SavedVideo.project_id.in_(ids)
            )
        )
        return set(rows.all())

    async def cards(session: AsyncSession, projects: list[Project]) -> list[dict[str, Any]]:
        """The details a video card shows, for every project, in one round of queries."""
        ids = [item.id for item in projects]
        lengths = await durations(session, ids)
        names = await authors(session, projects)
        progress = await progress_map(session, ids)
        saved = await saved_ids(session, ids)
        user_id = viewer()
        result = []
        for item in projects:
            watched = progress.get(item.id)
            result.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "description": item.description,
                    "subject": item.subject,
                    "tags": item.tags or [],
                    "level": item.level,
                    "visibility": item.visibility,
                    "views": item.view_count or 0,
                    "duration": lengths.get(item.id, 0.0),
                    "author": names.get(item.owner_id, "Ekalavya learner"),
                    "is_owner": user_id is not None and item.owner_id == user_id,
                    "saved": item.id in saved,
                    "status": item.status,
                    "text_language": item.text_language,
                    "voice_language": item.voice_language,
                    "published_at": item.published_at,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "progress": (
                        {
                            "position": watched.position_seconds,
                            "duration": watched.duration_seconds,
                            "completed": watched.completed,
                            "updated_at": watched.updated_at,
                        }
                        if watched
                        else None
                    ),
                }
            )
        return result

    async def public_videos(session: AsyncSession) -> list[Project]:
        return list(
            (
                await session.scalars(
                    select(Project).where(
                        Project.visibility == "public", Project.active_video_path.is_not(None)
                    )
                )
            ).all()
        )

    def popularity(item: Project) -> tuple:
        return (item.view_count or 0, _aware(item.published_at) or _aware(item.created_at))

    async def interest_vector(session: AsyncSession, user_id: UUID) -> list[float] | None:
        """What this learner has been studying: recent chats, saved videos, plan goals."""
        recent = (
            await session.scalars(
                select(Project.request)
                .where(Project.owner_id == user_id)
                .order_by(Project.updated_at.desc())
                .limit(8)
            )
        ).all()
        saved = (
            await session.scalars(
                select(Project.title)
                .join(SavedVideo, SavedVideo.project_id == Project.id)
                .where(SavedVideo.user_id == user_id)
                .limit(8)
            )
        ).all()
        goals = (
            await session.scalars(
                select(StudyPlan.goal).where(
                    StudyPlan.owner_id == user_id, StudyPlan.status == "active"
                )
            )
        ).all()
        text = "\n".join([*recent, *saved, *goals]).strip()
        return await embed_cached(text[:4000]) if text else None

    async def ensure_embedding(session: AsyncSession, project: Project) -> list[float] | None:
        if project.catalog_embedding:
            return project.catalog_embedding
        titles = (await scene_titles(session, [project.id])).get(project.id, [])
        vectors = await embed([catalog_text(project, titles)])
        if vectors and project.visibility == "public":
            await update_quietly(session, project, catalog_embedding=vectors[0])
        return vectors[0] if vectors else None

    def media_file(project: Project, version: int | None, name: str) -> Path | None:
        folder = Path(project.active_video_path).parent
        if version is not None:
            if project.owner_id != viewer():
                version = None  # Earlier versions stay private to the owner.
            else:
                folder = folder.parent / f"v{version}"
        candidate = folder / name
        return candidate if candidate.is_file() else None

    async def video_cues(project: Project) -> list[dict[str, Any]]:
        subtitles = media_file(project, None, "subtitles.vtt")
        if subtitles is None:
            return []
        return parse_vtt(subtitles.read_text(encoding="utf-8", errors="replace"))

    async def lesson_scenes(session: AsyncSession, project: Project) -> list[dict[str, Any]]:
        """Each scene of the current video with its narration, from the subtitles."""
        scenes = (
            await session.scalars(
                select(Scene)
                .where(Scene.project_id == project.id, Scene.timeline_start.is_not(None))
                .order_by(Scene.timeline_start)
            )
        ).all()
        cues = await video_cues(project)
        return [
            {
                "key": scene.scene_key,
                "title": scene.title,
                "start": scene.timeline_start,
                "end": scene.timeline_end,
                "narration": " ".join(
                    cue["text"]
                    for cue in cues
                    if scene.timeline_start <= cue["start"] < (scene.timeline_end or 0)
                ),
            }
            for scene in scenes
        ]

    async def write_catalog_entry(session: AsyncSession, project: Project) -> None:
        scenes = await lesson_scenes(session, project)
        with track_usage() as ledger:
            entry = await describe_video(
                create_llm_provider(settings),
                title=project.title,
                request=project.original_request or project.request,
                scenes=[scene["title"] for scene in scenes],
                narration=" ".join(scene["narration"] for scene in scenes),
                language=project.text_language,
            )
        await save_usage(session, ledger, project.id, "catalog")
        project.title = entry.title
        project.description = entry.description or project.description
        project.subject = entry.subject or project.subject
        project.tags = entry.tags or project.tags or []
        project.level = entry.level or project.level
        project.catalog_embedding = None

    # ------------------------------------------------------------ catalog

    @router.get("/v1/videos")
    async def search_videos(
        session: Session,
        q: str | None = Query(default=None, max_length=200),
        subject: str | None = Query(default=None, max_length=120),
        sort: str = Query(default="popular", pattern="^(popular|recent)$"),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        """Published videos, searched by keywords; meaning-based results when none match."""
        videos = await public_videos(session)
        if subject:
            videos = [
                item for item in videos if (item.subject or "").casefold() == subject.casefold()
            ]
        match = "all"
        if q and q.strip():
            titles = await scene_titles(session, [item.id for item in videos])
            names = await authors(session, videos)
            haystacks = {
                item.id: catalog_text(item, titles.get(item.id))
                + " "
                + names.get(item.owner_id, "")
                for item in videos
            }
            hits = [item for item in videos if keyword_match(haystacks[item.id], q)]
            if hits:
                videos, match = hits, "keyword"
            else:
                # No video has every word: rank by meaning instead, best first.
                query_vector = await embed_cached(q.strip())
                scored = []
                for item in videos:
                    score = _cosine(query_vector, await ensure_embedding(session, item))
                    if score > 0.3:
                        scored.append((score, item))
                scored.sort(key=lambda pair: pair[0], reverse=True)
                await session.commit()
                return {
                    "total": len(scored),
                    "match": "related",
                    "items": await cards(
                        session, [item for _, item in scored[offset : offset + limit]]
                    ),
                }
        if sort == "recent":
            videos.sort(
                key=lambda item: _aware(item.published_at) or _aware(item.created_at), reverse=True
            )
        else:
            videos.sort(key=popularity, reverse=True)
        return {
            "total": len(videos),
            "match": match,
            "items": await cards(session, videos[offset : offset + limit]),
        }

    @router.get("/v1/videos/subjects")
    async def subjects(session: Session):
        rows = await session.execute(
            select(Project.subject, func.count(Project.id))
            .where(
                Project.visibility == "public",
                Project.active_video_path.is_not(None),
                Project.subject.is_not(None),
                Project.subject != "",
            )
            .group_by(Project.subject)
            .order_by(func.count(Project.id).desc())
        )
        return [{"subject": subject, "count": count} for subject, count in rows.all()]

    async def recommended_for(session: AsyncSession, limit: int) -> list[Project]:
        user_id = viewer()
        candidates = [item for item in await public_videos(session) if item.owner_id != user_id]
        if user_id is not None:
            done = set(
                (
                    await session.scalars(
                        select(WatchProgress.project_id).where(
                            WatchProgress.user_id == user_id, WatchProgress.completed.is_(True)
                        )
                    )
                ).all()
            )
            candidates = [item for item in candidates if item.id not in done]
            interest = await interest_vector(session, user_id)
            if interest is not None:
                scored = [
                    (_cosine(interest, await ensure_embedding(session, item)), item)
                    for item in candidates
                ]
                await session.commit()
                scored.sort(key=lambda pair: (pair[0], popularity(pair[1])), reverse=True)
                return [item for _, item in scored[:limit]]
        candidates.sort(key=popularity, reverse=True)
        return candidates[:limit]

    @router.get("/v1/videos/recommended")
    async def recommended(session: Session, limit: int = Query(default=8, ge=1, le=40)):
        return await cards(session, await recommended_for(session, limit))

    @router.get("/v1/videos/{video_id}")
    async def video_detail(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        card = (await cards(session, [project]))[0]
        timeline_version = await session.scalar(
            select(func.max(TimelineSegment.video_version)).where(
                TimelineSegment.project_id == project.id
            )
        )
        scenes = await session.scalars(
            select(Scene)
            .where(Scene.project_id == project.id, Scene.timeline_start.is_not(None))
            .order_by(Scene.timeline_start)
        )
        return {
            **card,
            "request": project.original_request or project.request,
            "video_version": timeline_version,
            "chapters": [
                {
                    "scene_id": scene.scene_key,
                    "title": scene.title,
                    "start": scene.timeline_start,
                    "end": scene.timeline_end,
                }
                for scene in scenes.all()
            ],
        }

    @router.get("/v1/videos/{video_id}/related")
    async def related(video_id: UUID, session: Session, limit: int = Query(default=6, ge=1, le=24)):
        project = await open_video(session, video_id)
        own = await ensure_embedding(session, project)
        candidates = [item for item in await public_videos(session) if item.id != project.id]
        scored = [
            (_cosine(own, await ensure_embedding(session, item)), item) for item in candidates
        ]
        await session.commit()
        scored.sort(key=lambda pair: (pair[0], popularity(pair[1])), reverse=True)
        return await cards(session, [item for _, item in scored[:limit]])

    @router.get("/v1/videos/{video_id}/video")
    async def video_file(
        video_id: UUID, session: Session, version: int | None = Query(default=None, ge=1)
    ):
        project = await open_video(session, video_id)
        final = media_file(project, version, "final.mp4")
        if final is None:
            raise HTTPException(404, "video not ready")
        return FileResponse(final, media_type="video/mp4", filename=f"{project.title}.mp4")

    @router.get("/v1/videos/{video_id}/subtitles.vtt")
    async def video_subtitles(
        video_id: UUID, session: Session, version: int | None = Query(default=None, ge=1)
    ):
        project = await open_video(session, video_id)
        path = media_file(project, version, "subtitles.vtt")
        if path is None:
            raise HTTPException(404, "subtitles not ready")
        return FileResponse(path, media_type="text/vtt", headers={"Cache-Control": "no-cache"})

    @router.get("/v1/videos/{video_id}/chapters.vtt", response_class=PlainTextResponse)
    async def video_chapters(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        scenes = (
            await session.scalars(
                select(Scene)
                .where(Scene.project_id == project.id, Scene.timeline_start.is_not(None))
                .order_by(Scene.timeline_start)
            )
        ).all()

        def stamp(value: float) -> str:
            hours, remainder = divmod(value, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"

        cues = ["WEBVTT", ""]
        for index, scene in enumerate(scenes, 1):
            cues += [
                str(index),
                f"{stamp(scene.timeline_start)} --> {stamp(scene.timeline_end or 0)}",
                scene.title,
                "",
            ]
        return PlainTextResponse("\n".join(cues), media_type="text/vtt")

    @router.get("/v1/videos/{video_id}/transcript")
    async def transcript(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        return {"language": project.voice_language, "cues": await video_cues(project)}

    @router.get("/v1/videos/{video_id}/thumbnail.jpg")
    async def thumbnail(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        final = media_file(project, None, "final.mp4")
        if final is None:
            raise HTTPException(404, "video not ready")
        image = final.with_name("thumbnail.jpg")
        if not image.is_file():
            length = (await durations(session, [project.id])).get(project.id) or 0
            # A frame a third of the way in shows the lesson, not its title card.
            moment = max(length / 3, 0.5) if length else 1.0
            command = [
                settings.ffmpeg_binary,
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{moment:.2f}",
                "-i",
                str(final),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-q:v",
                "4",
                str(image),
            ]
            try:
                await asyncio.to_thread(subprocess.run, command, check=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as error:
                logger.warning("Thumbnail extraction failed for %s: %s", project.id, error)
                raise HTTPException(404, "thumbnail unavailable") from error
        return FileResponse(
            image, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"}
        )

    @router.post("/v1/videos/{video_id}/view", status_code=204)
    async def count_view(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        if project.owner_id != viewer():
            await update_quietly(session, project, view_count=(project.view_count or 0) + 1)
            await session.commit()

    @router.put("/v1/videos/{video_id}/progress")
    async def save_progress(video_id: UUID, payload: ProgressUpdate, session: Session):
        user_id = require_user()
        await open_video(session, video_id)
        record = await session.scalar(
            select(WatchProgress).where(
                WatchProgress.user_id == user_id, WatchProgress.project_id == video_id
            )
        )
        if record is None:
            record = WatchProgress(user_id=user_id, project_id=video_id)
            session.add(record)
        duration = payload.duration_seconds or record.duration_seconds or 0
        record.position_seconds = payload.position_seconds
        record.duration_seconds = duration
        # Within the last few seconds (the held end frame) counts as finished.
        record.completed = bool(duration) and payload.position_seconds >= duration - 3
        record.updated_at = datetime.now(UTC)
        await session.commit()
        return {"position": record.position_seconds, "completed": record.completed}

    @router.put("/v1/videos/{video_id}/save", status_code=204)
    async def save_video(video_id: UUID, session: Session):
        user_id = require_user()
        await open_video(session, video_id)
        exists = await session.scalar(
            select(SavedVideo.id).where(
                SavedVideo.user_id == user_id, SavedVideo.project_id == video_id
            )
        )
        if not exists:
            session.add(SavedVideo(user_id=user_id, project_id=video_id))
            await session.commit()

    @router.delete("/v1/videos/{video_id}/save", status_code=204)
    async def unsave_video(video_id: UUID, session: Session):
        user_id = require_user()
        await session.execute(
            delete(SavedVideo).where(
                SavedVideo.user_id == user_id, SavedVideo.project_id == video_id
            )
        )
        await session.commit()

    def note_payload(note: VideoNote) -> dict[str, Any]:
        return {
            "id": str(note.id),
            "video_id": str(note.project_id),
            "at_seconds": note.at_seconds,
            "text": note.text,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        }

    @router.get("/v1/videos/{video_id}/notes")
    async def list_notes(video_id: UUID, session: Session):
        user_id = require_user()
        await open_video(session, video_id)
        notes = await session.scalars(
            select(VideoNote)
            .where(VideoNote.user_id == user_id, VideoNote.project_id == video_id)
            .order_by(VideoNote.at_seconds, VideoNote.created_at)
        )
        return [note_payload(note) for note in notes.all()]

    @router.post("/v1/videos/{video_id}/notes", status_code=201)
    async def add_note(video_id: UUID, payload: NoteCreate, session: Session):
        user_id = require_user()
        await open_video(session, video_id)
        note = VideoNote(
            user_id=user_id,
            project_id=video_id,
            at_seconds=payload.at_seconds,
            text=payload.text.strip(),
        )
        session.add(note)
        await session.commit()
        await session.refresh(note)
        return note_payload(note)

    @router.get("/v1/videos/{video_id}/sources")
    async def video_sources(video_id: UUID, session: Session):
        project = await open_video(session, video_id)
        documents = await session.scalars(
            select(SourceDocument)
            .where(SourceDocument.project_id == project.id)
            .order_by(SourceDocument.created_at)
        )
        owner = project.owner_id == viewer()
        return [
            {
                "id": str(document.id),
                "title": document.title,
                "kind": document.kind,
                "url": (document.metadata_json or {}).get("url")
                or (document.uri if document.uri.startswith("http") else None),
                "downloadable": owner and not document.uri.startswith("http"),
                "created_at": document.created_at,
            }
            for document in documents.all()
        ]

    @router.get("/v1/videos/{video_id}/sources/{document_id}/file")
    async def source_file(video_id: UUID, document_id: UUID, session: Session):
        project = await session.get(Project, video_id)
        if project is None or project.owner_id != require_user():
            raise HTTPException(404, "chat not found")
        document = await session.get(SourceDocument, document_id)
        if document is None or document.project_id != project.id:
            raise HTTPException(404, "source not found")
        try:
            path = store.local_path(document.uri)
        except ValueError as error:
            raise HTTPException(404, "source file not found") from error
        if not path.is_file():
            raise HTTPException(404, "source file not found")
        return FileResponse(path, filename=path.name)

    @router.post("/v1/videos/{video_id}/describe")
    async def describe(video_id: UUID, session: Session):
        """Let the catalog agent (re)write this video's title, summary, subject and tags."""
        project = await owned_video(session, video_id)
        await write_catalog_entry(session, project)
        await session.commit()
        await session.refresh(project)
        return (await cards(session, [project]))[0]

    @router.patch("/v1/videos/{video_id}/catalog")
    async def update_catalog(video_id: UUID, payload: CatalogUpdate, session: Session):
        """Edit catalog details and publish or unpublish the video."""
        project = await owned_video(session, video_id)
        values = payload.model_dump(exclude_unset=True)
        if (
            values.get("visibility") == "public"
            and not project.description
            and not (values.get("description"))
        ):
            await write_catalog_entry(session, project)
        for field in ("title", "description", "subject", "level"):
            if field in values and values[field] is not None:
                setattr(project, field, values[field].strip())
        if values.get("tags") is not None:
            project.tags = list(dict.fromkeys(tag.strip() for tag in values["tags"] if tag.strip()))
        if values.get("visibility"):
            if values["visibility"] == "public" and project.visibility != "public":
                project.published_at = datetime.now(UTC)
            project.visibility = values["visibility"]
        project.catalog_embedding = None
        if project.visibility == "public":
            await ensure_embedding(session, project)
        await session.commit()
        await session.refresh(project)
        return (await cards(session, [project]))[0]

    @router.patch("/v1/notes/{note_id}")
    async def update_note(note_id: UUID, payload: NoteUpdate, session: Session):
        note = await session.get(VideoNote, note_id)
        if note is None or note.user_id != require_user():
            raise HTTPException(404, "note not found")
        if payload.text is not None:
            note.text = payload.text.strip()
        if payload.at_seconds is not None:
            note.at_seconds = payload.at_seconds
        note.updated_at = datetime.now(UTC)
        await session.commit()
        return note_payload(note)

    @router.delete("/v1/notes/{note_id}", status_code=204)
    async def delete_note(note_id: UUID, session: Session):
        note = await session.get(VideoNote, note_id)
        if note is None or note.user_id != require_user():
            raise HTTPException(404, "note not found")
        await session.delete(note)
        await session.commit()

    # ------------------------------------------------------------ library

    async def library_projects(session: AsyncSession, user_id: UUID) -> list[Project]:
        own = (
            await session.scalars(
                select(Project).where(
                    Project.owner_id == user_id, Project.active_video_path.is_not(None)
                )
            )
        ).all()
        saved = (
            await session.scalars(
                select(Project)
                .join(SavedVideo, SavedVideo.project_id == Project.id)
                .where(SavedVideo.user_id == user_id, Project.active_video_path.is_not(None))
            )
        ).all()
        merged = {item.id: item for item in [*own, *saved]}
        return [
            item
            for item in merged.values()
            if item.owner_id == user_id or item.visibility == "public"
        ]

    @router.get("/v1/library/videos")
    async def library_videos(
        session: Session,
        q: str | None = Query(default=None, max_length=200),
        filter: str = Query(default="all", pattern="^(all|mine|saved|public)$"),
    ):
        user_id = require_user()
        videos = await library_projects(session, user_id)
        if filter == "mine":
            videos = [item for item in videos if item.owner_id == user_id]
        elif filter == "saved":
            videos = [item for item in videos if item.owner_id != user_id]
        elif filter == "public":
            videos = [
                item for item in videos if item.owner_id == user_id and item.visibility == "public"
            ]
        if q and q.strip():
            titles = await scene_titles(session, [item.id for item in videos])
            videos = [
                item for item in videos if keyword_match(catalog_text(item, titles.get(item.id)), q)
            ]
        videos.sort(
            key=lambda item: _aware(item.updated_at) or _aware(item.created_at), reverse=True
        )
        return await cards(session, videos)

    @router.get("/v1/library/documents")
    async def library_documents(session: Session, q: str | None = Query(default=None)):
        user_id = require_user()
        rows = await session.execute(
            select(SourceDocument, Project)
            .join(Project, Project.id == SourceDocument.project_id)
            .where(Project.owner_id == user_id)
            .order_by(SourceDocument.created_at.desc())
        )
        documents = []
        for document, project in rows.all():
            if q and not keyword_match(f"{document.title} {project.title}", q):
                continue
            documents.append(
                {
                    "id": str(document.id),
                    "title": document.title,
                    "kind": document.kind,
                    "chat_id": str(project.id),
                    "chat_title": project.title,
                    "has_video": bool(project.active_video_path),
                    "url": (document.metadata_json or {}).get("url")
                    or (document.uri if document.uri.startswith("http") else None),
                    "downloadable": not document.uri.startswith("http"),
                    "created_at": document.created_at,
                }
            )
        return documents

    @router.get("/v1/library/notes")
    async def library_notes(session: Session, q: str | None = Query(default=None)):
        user_id = require_user()
        rows = await session.execute(
            select(VideoNote, Project)
            .join(Project, Project.id == VideoNote.project_id)
            .where(VideoNote.user_id == user_id)
            .order_by(VideoNote.updated_at.desc())
        )
        return [
            {**note_payload(note), "video_title": project.title}
            for note, project in rows.all()
            if not q or keyword_match(f"{note.text} {project.title}", q)
        ]

    async def owned_playlist(session: AsyncSession, playlist_id: UUID) -> Playlist:
        playlist = await session.get(Playlist, playlist_id)
        if playlist is None or playlist.owner_id != require_user():
            raise HTTPException(404, "playlist not found")
        return playlist

    async def playlist_payload(
        session: AsyncSession, playlist: Playlist, with_items: bool
    ) -> dict[str, Any]:
        items = (
            await session.scalars(
                select(Project)
                .join(PlaylistItem, PlaylistItem.project_id == Project.id)
                .where(PlaylistItem.playlist_id == playlist.id)
                .order_by(PlaylistItem.position, PlaylistItem.created_at)
            )
        ).all()
        visible = [
            item
            for item in items
            if item.active_video_path
            and (item.visibility == "public" or item.owner_id == playlist.owner_id)
        ]
        payload: dict[str, Any] = {
            "id": str(playlist.id),
            "name": playlist.name,
            "description": playlist.description,
            "count": len(visible),
            "cover_id": str(visible[0].id) if visible else None,
            "updated_at": playlist.updated_at,
        }
        if with_items:
            payload["items"] = await cards(session, visible)
        return payload

    @router.get("/v1/library/playlists")
    async def list_playlists(session: Session):
        playlists = await session.scalars(
            select(Playlist)
            .where(Playlist.owner_id == require_user())
            .order_by(Playlist.updated_at.desc())
        )
        return [await playlist_payload(session, item, False) for item in playlists.all()]

    @router.post("/v1/library/playlists", status_code=201)
    async def create_playlist(payload: PlaylistCreate, session: Session):
        playlist = Playlist(
            owner_id=require_user(),
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
        )
        session.add(playlist)
        await session.commit()
        await session.refresh(playlist)
        return await playlist_payload(session, playlist, True)

    @router.get("/v1/library/playlists/{playlist_id}")
    async def get_playlist(playlist_id: UUID, session: Session):
        return await playlist_payload(session, await owned_playlist(session, playlist_id), True)

    @router.patch("/v1/library/playlists/{playlist_id}")
    async def update_playlist(playlist_id: UUID, payload: PlaylistUpdate, session: Session):
        playlist = await owned_playlist(session, playlist_id)
        if payload.name is not None:
            playlist.name = payload.name.strip()
        if payload.description is not None:
            playlist.description = payload.description.strip() or None
        playlist.updated_at = datetime.now(UTC)
        await session.commit()
        return await playlist_payload(session, playlist, True)

    @router.delete("/v1/library/playlists/{playlist_id}", status_code=204)
    async def delete_playlist(playlist_id: UUID, session: Session):
        await session.delete(await owned_playlist(session, playlist_id))
        await session.commit()

    @router.post("/v1/library/playlists/{playlist_id}/items", status_code=201)
    async def add_playlist_item(playlist_id: UUID, payload: PlaylistItemAdd, session: Session):
        playlist = await owned_playlist(session, playlist_id)
        await open_video(session, payload.video_id)
        exists = await session.scalar(
            select(PlaylistItem.id).where(
                PlaylistItem.playlist_id == playlist.id,
                PlaylistItem.project_id == payload.video_id,
            )
        )
        if not exists:
            last = await session.scalar(
                select(func.max(PlaylistItem.position)).where(
                    PlaylistItem.playlist_id == playlist.id
                )
            )
            session.add(
                PlaylistItem(
                    playlist_id=playlist.id,
                    project_id=payload.video_id,
                    position=(last or 0) + 1,
                )
            )
            playlist.updated_at = datetime.now(UTC)
            await session.commit()
        return await playlist_payload(session, playlist, True)

    @router.delete("/v1/library/playlists/{playlist_id}/items/{video_id}", status_code=204)
    async def remove_playlist_item(playlist_id: UUID, video_id: UUID, session: Session):
        playlist = await owned_playlist(session, playlist_id)
        await session.execute(
            delete(PlaylistItem).where(
                PlaylistItem.playlist_id == playlist.id, PlaylistItem.project_id == video_id
            )
        )
        playlist.updated_at = datetime.now(UTC)
        await session.commit()

    # ------------------------------------------------------------ study plans

    def task_payload(task: StudyTask, plan: StudyPlan | None = None) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "plan_id": str(task.plan_id),
            "plan_title": plan.title if plan else None,
            "due_date": _aware(task.due_date).date().isoformat(),
            "kind": task.kind,
            "title": task.title,
            "detail": task.detail,
            "minutes": task.minutes,
            "video_id": str(task.project_id) if task.project_id else None,
            "topic": task.topic,
            "done": task.done,
            "done_at": task.done_at,
        }

    async def plan_payload(session: AsyncSession, plan: StudyPlan, with_tasks: bool) -> dict:
        tasks = (
            await session.scalars(
                select(StudyTask)
                .where(StudyTask.plan_id == plan.id)
                .order_by(StudyTask.due_date, StudyTask.position)
            )
        ).all()
        payload: dict[str, Any] = {
            "id": str(plan.id),
            "goal": plan.goal,
            "title": plan.title,
            "summary": plan.summary,
            "start_date": _aware(plan.start_date).date().isoformat(),
            "end_date": _aware(plan.end_date).date().isoformat() if plan.end_date else None,
            "minutes_per_day": plan.minutes_per_day,
            "language": plan.language,
            "status": plan.status,
            "task_count": len(tasks),
            "done_count": sum(task.done for task in tasks),
            "created_at": plan.created_at,
        }
        if with_tasks:
            payload["tasks"] = [task_payload(task) for task in tasks]
        return payload

    async def owned_plan(session: AsyncSession, plan_id: UUID) -> StudyPlan:
        plan = await session.get(StudyPlan, plan_id)
        if plan is None or plan.owner_id != require_user():
            raise HTTPException(404, "study plan not found")
        return plan

    async def plannable_videos(session: AsyncSession, user_id: UUID, goal: str) -> list[dict]:
        """The learner's own and saved videos, plus the public ones closest to the goal."""
        mine = await library_projects(session, user_id)
        mine_ids = {item.id for item in mine}
        goal_vector = await embed_cached(goal[:2000])
        others = [item for item in await public_videos(session) if item.id not in mine_ids]
        scored = sorted(
            [
                (_cosine(goal_vector, await ensure_embedding(session, item)), item)
                for item in others
            ],
            key=lambda pair: pair[0],
            reverse=True,
        )
        await session.commit()
        chosen = [*mine, *[item for _, item in scored[:25]]]
        lengths = await durations(session, [item.id for item in chosen])
        return [
            {
                "id": str(item.id),
                "title": item.title,
                "subject": item.subject,
                "duration": lengths.get(item.id),
                "note": "learner's own"
                if item.owner_id == user_id
                else ("saved" if item.id in mine_ids else "public"),
            }
            for item in chosen
        ]

    async def store_tasks(session: AsyncSession, plan: StudyPlan, draft, start: date) -> None:
        for position, task in enumerate(draft.tasks):
            session.add(
                StudyTask(
                    plan_id=plan.id,
                    due_date=datetime.combine(
                        start + timedelta(days=task.day), datetime.min.time(), UTC
                    ),
                    kind=task.kind,
                    title=task.title.strip()[:300],
                    detail=task.detail.strip() or None,
                    minutes=task.minutes,
                    project_id=UUID(task.video_id) if task.video_id else None,
                    topic=(task.topic or "").strip() or None,
                    position=position,
                )
            )

    @router.get("/v1/study-plans")
    async def list_plans(session: Session, status: str = Query(default="active")):
        plans = await session.scalars(
            select(StudyPlan)
            .where(StudyPlan.owner_id == require_user(), StudyPlan.status == status)
            .order_by(StudyPlan.created_at.desc())
        )
        return [await plan_payload(session, plan, False) for plan in plans.all()]

    @router.get("/v1/study-plans/tasks")
    async def tasks_between(
        session: Session, start: Annotated[date, Query()], end: Annotated[date, Query()]
    ):
        """Tasks of the learner's active plans due in a date range (inclusive)."""
        user_id = require_user()
        rows = await session.execute(
            select(StudyTask, StudyPlan)
            .join(StudyPlan, StudyPlan.id == StudyTask.plan_id)
            .where(
                StudyPlan.owner_id == user_id,
                StudyPlan.status == "active",
                StudyTask.due_date >= datetime.combine(start, datetime.min.time(), UTC),
                StudyTask.due_date
                < datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC),
            )
            .order_by(StudyTask.due_date, StudyTask.position)
        )
        return [task_payload(task, plan) for task, plan in rows.all()]

    @router.post("/v1/study-plans", status_code=201)
    async def create_plan(payload: StudyPlanCreate, session: Session):
        user_id = require_user()
        account = await session.get(UserAccount, user_id)
        start = payload.start_date or datetime.now(UTC).date()
        if payload.end_date is not None and payload.end_date < start:
            raise HTTPException(422, "the end date is before the start date")
        language = payload.language or (account.default_text_language if account else "English")
        videos = await plannable_videos(session, user_id, payload.goal)
        with track_usage() as ledger:
            try:
                draft = await plan_study(
                    create_llm_provider(settings),
                    goal=payload.goal.strip(),
                    start=start,
                    end=payload.end_date,
                    minutes_per_day=payload.minutes_per_day,
                    language=language,
                    videos=videos,
                )
            except StudyPlanUnavailable as error:
                raise HTTPException(503, f"The study planner is unavailable: {error}") from error
        await save_usage(session, ledger, None, "study_plan")
        last_day = max((task.day for task in draft.tasks), default=0)
        plan = StudyPlan(
            owner_id=user_id,
            goal=payload.goal.strip(),
            title=draft.title.strip()[:300] or payload.goal.strip()[:300],
            summary=draft.summary.strip() or None,
            start_date=datetime.combine(start, datetime.min.time(), UTC),
            end_date=datetime.combine(
                payload.end_date or start + timedelta(days=last_day), datetime.min.time(), UTC
            ),
            minutes_per_day=payload.minutes_per_day,
            language=language,
        )
        session.add(plan)
        await session.flush()
        await store_tasks(session, plan, draft, start)
        await session.commit()
        return await plan_payload(session, plan, True)

    @router.get("/v1/study-plans/{plan_id}")
    async def get_plan(plan_id: UUID, session: Session):
        return await plan_payload(session, await owned_plan(session, plan_id), True)

    @router.patch("/v1/study-plans/{plan_id}")
    async def update_plan(plan_id: UUID, payload: StudyPlanUpdate, session: Session):
        plan = await owned_plan(session, plan_id)
        if payload.title is not None:
            plan.title = payload.title.strip()
        if payload.status is not None:
            plan.status = payload.status
        plan.updated_at = datetime.now(UTC)
        await session.commit()
        return await plan_payload(session, plan, True)

    @router.delete("/v1/study-plans/{plan_id}", status_code=204)
    async def delete_plan(plan_id: UUID, session: Session):
        await session.delete(await owned_plan(session, plan_id))
        await session.commit()

    @router.post("/v1/study-plans/{plan_id}/replan")
    async def replan(plan_id: UUID, payload: ReplanRequest, session: Session):
        """Keep finished tasks; the planner reschedules everything still to do from today."""
        user_id = require_user()
        plan = await owned_plan(session, plan_id)
        tasks = (
            await session.scalars(
                select(StudyTask).where(StudyTask.plan_id == plan.id).order_by(StudyTask.due_date)
            )
        ).all()
        today = datetime.now(UTC).date()
        progress = "\n".join(
            f"- {'DONE' if task.done else 'NOT DONE'} ({_aware(task.due_date).date()}): "
            f"{task.kind} - {task.title}"
            for task in tasks
        )
        videos = await plannable_videos(session, user_id, plan.goal)
        end = _aware(plan.end_date).date() if plan.end_date else None
        if end is not None and end < today:
            end = None
        with track_usage() as ledger:
            try:
                draft = await plan_study(
                    create_llm_provider(settings),
                    goal=plan.goal,
                    start=today,
                    end=end,
                    minutes_per_day=plan.minutes_per_day,
                    language=plan.language,
                    videos=videos,
                    progress=progress,
                    instruction=payload.instruction.strip(),
                )
            except StudyPlanUnavailable as error:
                raise HTTPException(503, f"The study planner is unavailable: {error}") from error
        await save_usage(session, ledger, None, "study_plan")
        for task in tasks:
            if not task.done:
                await session.delete(task)
        await store_tasks(session, plan, draft, today)
        if draft.summary.strip():
            plan.summary = draft.summary.strip()
        last_day = max((task.day for task in draft.tasks), default=0)
        plan.end_date = datetime.combine(
            end or today + timedelta(days=last_day), datetime.min.time(), UTC
        )
        plan.updated_at = datetime.now(UTC)
        await session.commit()
        return await plan_payload(session, plan, True)

    @router.patch("/v1/study-plans/tasks/{task_id}")
    async def update_task(task_id: UUID, payload: TaskUpdate, session: Session):
        task = await session.get(StudyTask, task_id)
        plan = await session.get(StudyPlan, task.plan_id) if task else None
        if task is None or plan is None or plan.owner_id != require_user():
            raise HTTPException(404, "task not found")
        if payload.done is not None:
            task.done = payload.done
            task.done_at = datetime.now(UTC) if payload.done else None
        if payload.due_date is not None:
            task.due_date = datetime.combine(payload.due_date, datetime.min.time(), UTC)
        await session.commit()
        return task_payload(task, plan)

    # ------------------------------------------------------------ quizzes

    def public_question(question: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": question["kind"],
            "prompt": question["prompt"],
            "options": question.get("options") or [],
            "scene_key": question.get("scene_key"),
        }

    async def quiz_payload(session: AsyncSession, quiz: Quiz) -> dict[str, Any]:
        attempts = (
            await session.scalars(
                select(QuizAttempt)
                .where(QuizAttempt.quiz_id == quiz.id)
                .order_by(QuizAttempt.created_at.desc())
            )
        ).all()
        latest = attempts[0] if attempts else None
        return {
            "id": str(quiz.id),
            "title": quiz.title,
            "video_id": str(quiz.project_id) if quiz.project_id else None,
            "topic": quiz.topic,
            "language": quiz.language,
            "questions": [public_question(question) for question in quiz.questions],
            "attempts": len(attempts),
            "best_score": max((attempt.score for attempt in attempts), default=None),
            "latest": (
                {
                    "answers": latest.answers,
                    "results": latest.results,
                    "score": latest.score,
                    "created_at": latest.created_at,
                }
                if latest
                else None
            ),
            "created_at": quiz.created_at,
        }

    @router.get("/v1/quizzes")
    async def list_quizzes(session: Session, video_id: Annotated[UUID | None, Query()] = None):
        statement = select(Quiz).where(Quiz.owner_id == require_user())
        if video_id is not None:
            statement = statement.where(Quiz.project_id == video_id)
        quizzes = await session.scalars(statement.order_by(Quiz.created_at.desc()).limit(50))
        return [await quiz_payload(session, quiz) for quiz in quizzes.all()]

    @router.post("/v1/quizzes", status_code=201)
    async def create_quiz(payload: QuizCreate, session: Session):
        user_id = require_user()
        account = await session.get(UserAccount, user_id)
        project = None
        scenes: list[dict] = []
        if payload.video_id is not None:
            project = await open_video(session, payload.video_id)
            scenes = await lesson_scenes(session, project)
        topic = (payload.topic or "").strip()
        if project is None and not topic:
            raise HTTPException(422, "choose a video or give a topic for the quiz")
        language = (
            payload.language
            or (project.text_language if project else None)
            or (account.default_text_language if account else "English")
        )
        title = project.title if project else topic
        with track_usage() as ledger:
            try:
                draft = await write_quiz(
                    create_llm_provider(settings),
                    title=title,
                    topic=topic or (project.original_request or project.request),
                    scenes=scenes,
                    language=language,
                    instruction=payload.instruction.strip(),
                )
            except QuizUnavailable as error:
                raise HTTPException(503, f"The quiz writer is unavailable: {error}") from error
        await save_usage(session, ledger, project.id if project else None, "quiz")
        quiz = Quiz(
            owner_id=user_id,
            project_id=project.id if project else None,
            topic=topic or None,
            title=draft.title.strip()[:300] or title[:300],
            language=language,
            questions=[question.model_dump() for question in draft.questions],
        )
        session.add(quiz)
        await session.commit()
        await session.refresh(quiz)
        return await quiz_payload(session, quiz)

    async def owned_quiz(session: AsyncSession, quiz_id: UUID) -> Quiz:
        quiz = await session.get(Quiz, quiz_id)
        if quiz is None or quiz.owner_id != require_user():
            raise HTTPException(404, "quiz not found")
        return quiz

    @router.get("/v1/quizzes/{quiz_id}")
    async def get_quiz(quiz_id: UUID, session: Session):
        return await quiz_payload(session, await owned_quiz(session, quiz_id))

    @router.delete("/v1/quizzes/{quiz_id}", status_code=204)
    async def delete_quiz(quiz_id: UUID, session: Session):
        await session.delete(await owned_quiz(session, quiz_id))
        await session.commit()

    @router.post("/v1/quizzes/{quiz_id}/attempts")
    async def submit_quiz(quiz_id: UUID, payload: QuizSubmission, session: Session):
        quiz = await owned_quiz(session, quiz_id)
        questions = quiz.questions
        answers = list(payload.answers) + [None] * (len(questions) - len(payload.answers))
        answers = answers[: len(questions)]
        starts: dict[str, float] = {}
        if quiz.project_id:
            rows = await session.execute(
                select(Scene.scene_key, Scene.timeline_start).where(
                    Scene.project_id == quiz.project_id, Scene.timeline_start.is_not(None)
                )
            )
            starts = dict(rows.all())
        written = {
            index: str(answer).strip()
            for index, (question, answer) in enumerate(zip(questions, answers, strict=True))
            if question["kind"] == "short" and answer is not None and str(answer).strip()
        }
        with track_usage() as ledger:
            try:
                graded = await grade_written_answers(
                    create_llm_provider(settings),
                    questions=questions,
                    answers=written,
                    language=quiz.language,
                )
            except QuizUnavailable as error:
                raise HTTPException(503, f"Grading is unavailable: {error}") from error
        await save_usage(session, ledger, quiz.project_id, "quiz")
        results = []
        for index, (question, answer) in enumerate(zip(questions, answers, strict=True)):
            if question["kind"] == "choice":
                correct = isinstance(answer, int) and answer == question.get("answer_index")
                feedback = ""
            else:
                verdict = graded.get(index)
                correct = bool(verdict and verdict.correct)
                feedback = verdict.feedback if verdict else ""
            scene_key = question.get("scene_key")
            results.append(
                {
                    "correct": correct,
                    "feedback": feedback,
                    "explanation": question.get("explanation") or "",
                    "answer_index": question.get("answer_index"),
                    "answer": question.get("answer") or "",
                    "scene_key": scene_key,
                    "at_seconds": starts.get(scene_key) if scene_key else None,
                }
            )
        score = sum(item["correct"] for item in results) / len(results) if results else 0.0
        session.add(
            QuizAttempt(
                quiz_id=quiz.id,
                user_id=quiz.owner_id,
                answers=answers,
                results=results,
                score=score,
            )
        )
        await session.commit()
        return {"score": score, "results": results}

    # ------------------------------------------------------------ home

    @router.get("/v1/home")
    async def home(session: Session):
        user_id = require_user()
        progress = (
            await session.scalars(
                select(WatchProgress)
                .where(WatchProgress.user_id == user_id, WatchProgress.completed.is_(False))
                .order_by(WatchProgress.updated_at.desc())
                .limit(12)
            )
        ).all()
        resumable = [
            project
            for project in [await session.get(Project, item.project_id) for item in progress]
            if project
            and project.active_video_path
            and (project.owner_id == user_id or project.visibility == "public")
        ]
        today = datetime.now(UTC).date()
        rows = await session.execute(
            select(StudyTask, StudyPlan)
            .join(StudyPlan, StudyPlan.id == StudyTask.plan_id)
            .where(
                StudyPlan.owner_id == user_id,
                StudyPlan.status == "active",
                StudyTask.due_date
                < datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC),
                StudyTask.done.is_(False),
            )
            .order_by(StudyTask.due_date, StudyTask.position)
            .limit(8)
        )
        recent = (
            await session.scalars(
                select(Project)
                .where(Project.owner_id == user_id, Project.archived.is_(False))
                .order_by(Project.updated_at.desc())
                .limit(6)
            )
        ).all()
        return {
            "continue": await cards(session, resumable[:8]),
            "recommended": await cards(session, await recommended_for(session, 8)),
            "today": [task_payload(task, plan) for task, plan in rows.all()],
            "recent_chats": [
                {
                    "id": str(item.id),
                    "title": item.title,
                    "status": item.status,
                    "has_video": bool(item.active_video_path),
                    "updated_at": item.updated_at,
                }
                for item in recent
            ],
        }

    return router
