"""FastAPI routes: accounts, chats, sources, builds, edits, media, and capabilities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.agents.chat_title import title_for_request
from scholarmotion.agents.learner_profiler import DEFAULT_VIDEO_DURATION_MINUTES
from scholarmotion.agents.lesson_preparation import prepare_lesson
from scholarmotion.api.admin import require_admin
from scholarmotion.api.capabilities import create_capability_router
from scholarmotion.api.knowledge import KNOWLEDGE_PREFIX, create_knowledge_router
from scholarmotion.api.learning import (
    MEDIA_SUFFIXES,
    OPTIONAL_AUTH_PREFIXES,
    REQUIRED_AUTH_PREFIXES,
    create_learning_router,
)
from scholarmotion.api.service import (
    activate_video_version,
    openrouter_prices,
    reassemble_active,
    reset_failed_generation,
)
from scholarmotion.api.tutor import (
    chat_history,
    edit_range_for,
    planner_scenes,
    respond_ephemeral,
    video_scenes,
)
from scholarmotion.api.tutor import respond as tutor_respond
from scholarmotion.api.usage import create_usage_router
from scholarmotion.config import get_settings
from scholarmotion.observability import (
    configure_logging,
    configure_telemetry,
    get_logger,
    observe_http,
    prometheus_payload,
)
from scholarmotion.observability.usage import track_usage
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import (
    AdministrationAudit,
    AudioArtifact,
    CorrectionCandidate,
    CorrectionEntry,
    GeneratedCode,
    ProgressEvent,
    Project,
    ProjectFolder,
    ProjectRevision,
    RenderArtifact,
    Scene,
    SceneSpecification,
    SceneVersion,
    Skill,
    SourceDocument,
    TimelineSegment,
    TutorMessage,
    UsageEvent,
    UserAccount,
    UserSession,
    WorkflowExecution,
    WorkspaceProfile,
    update_quietly,
)
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantUnavailable, QdrantVectorStore
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_llm_provider,
    create_machine_translator,
    create_planner_provider,
    create_translation_provider,
)
from scholarmotion.retrieval.source_ingestion import (
    LinkUnreadable,
    index_source,
    ingest_link,
    parse_stored_pdf,
)
from scholarmotion.schemas import (
    EditRangeRequest,
    ProjectCopyRequest,
    ProjectCreate,
    ProjectFolderCreate,
    ProjectFolderUpdate,
    ProjectMoveRequest,
    ProjectSettingsUpdate,
    ProjectUpdate,
    SourceLinkRequest,
    TemporaryChatRequest,
    TutorChatRequest,
    TutorChatResponse,
    TutorMessageEdit,
    UserAccountUpdate,
    UserLoginRequest,
    UserRegisterRequest,
)
from scholarmotion.services import research_settings as research
from scholarmotion.services.chat_memory import compact, forget_from
from scholarmotion.services.index_reconciliation import reconcile_qdrant_index
from scholarmotion.services.usage_records import new_run_id, usage_rows
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge
from scholarmotion.translation import TranslationService
from scholarmotion.workflows import (
    cancel_workflow,
    get_workflow_status,
    start_build_workflow,
    start_edit_workflow,
    start_localize_workflow,
)

settings = get_settings()
database = Database(settings.database_url)
store = LocalObjectStore(settings.object_storage_root)
configure_logging()
logger = get_logger(__name__)
current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return (
        "scrypt$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(digest).decode()
    )


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        actual = _hash_password(password, salt).split("$", 2)[2]
        return hmac.compare_digest(actual, digest_text)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.casefold() == "bearer" and token.strip() else None


async def _user_from_token(
    session: AsyncSession, token: str | None
) -> tuple[UserAccount, UserSession] | None:
    if not token:
        return None
    value = await session.execute(
        select(UserAccount, UserSession)
        .join(UserSession, UserSession.user_id == UserAccount.id)
        .where(
            UserSession.token_hash == _token_hash(token),
            UserSession.revoked.is_(False),
            UserAccount.active.is_(True),
        )
    )
    row = value.one_or_none()
    if row is None:
        return None
    user, login_session = row
    expires_at = login_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return None if expires_at <= datetime.now(UTC) else (user, login_session)


def _user_payload(user: UserAccount) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_color": user.avatar_color,
        "default_duration_minutes": user.default_duration_minutes,
        "default_text_language": user.default_text_language,
        "default_voice_language": user.default_voice_language,
        "research_settings": research.resolve(user.research_settings).model_dump(),
        "is_admin": user.username.casefold()
        in {name.strip().casefold() for name in settings.admin_usernames.split(",")},
    }


async def _issue_session(session: AsyncSession, user: UserAccount) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    return token


def _context_user_id() -> UUID:
    user_id = current_user_id.get()
    if user_id is None:
        raise HTTPException(401, "authentication required")
    return user_id


async def _owned_project(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == _context_user_id())
    )
    if project is None:
        raise HTTPException(404, "project not found")
    return project


async def _owned_folder(session: AsyncSession, folder_id: UUID) -> ProjectFolder:
    folder = await session.scalar(
        select(ProjectFolder).where(
            ProjectFolder.id == folder_id,
            ProjectFolder.owner_id == _context_user_id(),
        )
    )
    if folder is None:
        raise HTTPException(404, "project folder not found")
    return folder


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.create_all()
    async with database.sessions() as session:
        await bootstrap_system_knowledge(session)
        await session.commit()
    yield
    await database.close()


app = FastAPI(title="Ekalavya API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.media_cors_origins.split(",") if origin.strip()
    ],
    allow_methods=["GET"],
    allow_headers=["Range"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length"],
)
configure_telemetry(
    endpoint=settings.otel_exporter_otlp_endpoint,
    service_name=settings.otel_service_name,
    app=app,
)


@app.middleware("http")
async def request_observability(request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id", str(UUID(int=time.time_ns() % (1 << 128))))
    if settings.app_api_key and request.url.path not in {"/health", "/docs", "/openapi.json"}:
        supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if supplied != settings.app_api_key:
            return JSONResponse({"detail": "missing or invalid X-API-Key"}, status_code=401)
    auth_context = None
    path = request.url.path
    required = path == "/projects" or path.startswith(
        (
            "/projects/",
            "/v1/projects",
            "/v1/project-folders",
            "/v1/temporary-chat",
            "/v1/usage",
            *REQUIRED_AUTH_PREFIXES,
            KNOWLEDGE_PREFIX,
        )
    )
    # Published videos are open to anyone; a signed-in viewer also sees their own.
    optional = path.startswith(OPTIONAL_AUTH_PREFIXES)
    if required or optional:
        raw_token = _bearer_token(request.headers.get("authorization"))
        if raw_token is None and path.endswith(MEDIA_SUFFIXES):
            # Browsers cannot add headers to <video>/<img>/<track> requests.
            raw_token = request.query_params.get("access_token")
        authenticated = None
        if raw_token is not None:
            async with database.sessions() as auth_session:
                authenticated = await _user_from_token(auth_session, raw_token)
        if authenticated is None and required:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        if authenticated is not None:
            auth_context = current_user_id.set(authenticated[0].id)
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        observe_http(method=request.method, status=500, duration_seconds=duration_ms / 1000)
        raise
    finally:
        if auth_context is not None:
            current_user_id.reset(auth_context)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    observe_http(
        method=request.method,
        status=response.status_code,
        duration_seconds=duration_ms / 1000,
    )
    response.headers["x-request-id"] = request_id
    logger.info(
        "request.complete",
        extra={"event": "request.complete", "request_id": request_id, "duration_ms": duration_ms},
    )
    return response


async def session_dependency():
    async with database.sessions() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SourceFile = Annotated[UploadFile, File()]
Authorization = Annotated[str | None, Header()]
app.include_router(create_capability_router(settings, store, session_dependency))
app.include_router(create_usage_router(settings, session_dependency, current_user_id))
app.include_router(create_learning_router(settings, store, session_dependency, current_user_id))
app.include_router(create_knowledge_router(settings, session_dependency, current_user_id))


@app.get("/health")
async def health():
    return {"status": "ok", "mode": settings.main_llm_provider}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return PlainTextResponse(prometheus_payload(), media_type="text/plain; version=0.0.4")


@app.post("/v1/auth/register", status_code=201)
async def register_user(payload: UserRegisterRequest, session: SessionDep):
    username = payload.username.strip().casefold()
    if await session.scalar(select(UserAccount.id).where(UserAccount.username == username)):
        raise HTTPException(409, "username is already registered")
    user = UserAccount(
        username=username,
        password_hash=_hash_password(payload.password),
        display_name=(payload.display_name or payload.username).strip(),
    )
    session.add(user)
    await session.flush()
    token = await _issue_session(session, user)
    await session.commit()
    await session.refresh(user)
    return {"token": token, "token_type": "bearer", "user": _user_payload(user)}


@app.post("/v1/auth/login")
async def login_user(payload: UserLoginRequest, session: SessionDep):
    user = await session.scalar(
        select(UserAccount).where(UserAccount.username == payload.username.strip().casefold())
    )
    if (
        user is None
        or not user.active
        or not _password_matches(payload.password, user.password_hash)
    ):
        raise HTTPException(401, "invalid username or password")
    token = await _issue_session(session, user)
    await session.commit()
    return {"token": token, "token_type": "bearer", "user": _user_payload(user)}


@app.get("/v1/auth/me")
async def authenticated_user(session: SessionDep, authorization: Authorization = None):
    authenticated = await _user_from_token(session, _bearer_token(authorization))
    if authenticated is None:
        raise HTTPException(401, "authentication required")
    return _user_payload(authenticated[0])


_CREDIT_CACHE_SECONDS = 60
_credit_cache: dict[str, tuple[float, dict]] = {}


async def _openrouter_credit(key: str) -> dict:
    """Remaining balance for an OpenRouter key: its own limit, else the account's credits."""
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(base_url="https://openrouter.ai/api/v1", timeout=15) as client:
        data = (await client.get("/key", headers=headers)).raise_for_status().json()["data"]
        if data.get("limit") is not None:
            return {
                "remaining_usd": data.get("limit_remaining"),
                "limit_usd": data["limit"],
                "used_usd": data.get("usage"),
            }
        account = (await client.get("/credits", headers=headers)).raise_for_status().json()["data"]
        total, used = account["total_credits"], account["total_usage"]
        return {"remaining_usd": total - used, "limit_usd": total, "used_usd": used}


@app.get("/v1/credits")
async def provider_credits(session: SessionDep, authorization: Authorization = None):
    """Model-provider credit left on the server's key; the key itself never leaves the API."""
    if await _user_from_token(session, _bearer_token(authorization)) is None:
        raise HTTPException(401, "authentication required")
    key = settings.openrouter_api_key or settings.main_llm_api_key
    if not key or "openrouter.ai" not in (settings.main_llm_base_url or "https://openrouter.ai"):
        return {"available": False}
    cached = _credit_cache.get(key)
    if cached and time.monotonic() - cached[0] < _CREDIT_CACHE_SECONDS:
        return cached[1]
    try:
        payload = {"available": True, "provider": "OpenRouter", **await _openrouter_credit(key)}
    except (httpx.HTTPError, KeyError, TypeError) as error:
        logger.warning("Credit lookup failed: %s", error)
        return {"available": False}
    _credit_cache[key] = (time.monotonic(), payload)
    return payload


@app.patch("/v1/auth/me")
async def update_authenticated_user(
    payload: UserAccountUpdate,
    session: SessionDep,
    authorization: Authorization = None,
):
    authenticated = await _user_from_token(session, _bearer_token(authorization))
    if authenticated is None:
        raise HTTPException(401, "authentication required")
    user = authenticated[0]
    values = payload.model_dump(exclude_none=True)
    if "research_settings" in values:
        merged = {**(user.research_settings or {}), **values.pop("research_settings")}
        user.research_settings = research.resolve(merged).model_dump()
    for field, value in values.items():
        setattr(user, field, value.strip() if isinstance(value, str) else value)
    await session.commit()
    await session.refresh(user)
    return _user_payload(user)


@app.get("/v1/research/options")
async def research_options(session: SessionDep, authorization: Authorization = None):
    """The research-depth settings with ranges, and the measured cost of one step of each."""
    if await _user_from_token(session, _bearer_token(authorization)) is None:
        raise HTTPException(401, "authentication required")
    since = datetime.now(UTC) - timedelta(days=30)
    rows = (
        await session.execute(
            select(UsageEvent.purpose, func.sum(UsageEvent.cost_usd), func.count())
            .where(UsageEvent.created_at >= since, UsageEvent.cost_usd.is_not(None))
            .group_by(UsageEvent.purpose)
        )
    ).all()
    # Purposes are labelled "stage:purpose"; a knob is measured by the call kind.
    totals: dict[str, list[float]] = {}
    for purpose, cost, calls in rows:
        kind = purpose.rpartition(":")[2]
        totals.setdefault(kind, [0.0, 0])
        totals[kind][0] += float(cost or 0)
        totals[kind][1] += calls
    measured = {kind: cost / calls for kind, (cost, calls) in totals.items() if calls}
    return {"settings": research.describe(measured)}


@app.post("/v1/auth/logout", status_code=204)
async def logout_user(session: SessionDep, authorization: Authorization = None):
    authenticated = await _user_from_token(session, _bearer_token(authorization))
    if authenticated is not None:
        authenticated[1].revoked = True
        await session.commit()


@app.get("/v1/project-folders")
async def list_project_folders(session: SessionDep):
    folders = (
        await session.scalars(
            select(ProjectFolder)
            .where(ProjectFolder.owner_id == _context_user_id())
            .order_by(ProjectFolder.updated_at.desc(), ProjectFolder.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(folder.id),
            "name": folder.name,
            "chat_count": await session.scalar(
                select(func.count(Project.id)).where(Project.folder_id == folder.id)
            )
            or 0,
            "created_at": folder.created_at,
            "updated_at": folder.updated_at,
        }
        for folder in folders
    ]


@app.post("/v1/project-folders", status_code=201)
async def create_project_folder(payload: ProjectFolderCreate, session: SessionDep):
    name = payload.name.strip()
    duplicate = await session.scalar(
        select(ProjectFolder.id).where(
            ProjectFolder.owner_id == _context_user_id(),
            func.lower(ProjectFolder.name) == name.casefold(),
        )
    )
    if duplicate:
        raise HTTPException(409, "a project with this name already exists")
    folder = ProjectFolder(owner_id=_context_user_id(), name=name)
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    return {"id": str(folder.id), "name": folder.name, "chat_count": 0}


@app.patch("/v1/project-folders/{folder_id}")
async def rename_project_folder(folder_id: UUID, payload: ProjectFolderUpdate, session: SessionDep):
    folder = await _owned_folder(session, folder_id)
    name = payload.name.strip()
    duplicate = await session.scalar(
        select(ProjectFolder.id).where(
            ProjectFolder.owner_id == _context_user_id(),
            ProjectFolder.id != folder_id,
            func.lower(ProjectFolder.name) == name.casefold(),
        )
    )
    if duplicate:
        raise HTTPException(409, "a project with this name already exists")
    folder.name = name
    folder.updated_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(folder.id), "name": folder.name}


@app.delete("/v1/project-folders/{folder_id}", status_code=204)
async def delete_project_folder(folder_id: UUID, session: SessionDep):
    folder = await _owned_folder(session, folder_id)
    await session.delete(folder)
    await session.commit()


async def name_chat(project_id: UUID, owner_id: UUID | None, provisional: str) -> None:
    """Give a new chat a model-written title, unless the learner renamed it meanwhile."""
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        with track_usage() as ledger:
            title = await title_for_request(
                create_llm_provider(settings), project.original_request or project.request
            )
        if ledger.entries:
            try:
                if any(entry.cost_usd is None for entry in ledger.entries):
                    ledger.estimate_missing_costs(await openrouter_prices(settings))
                session.add_all(
                    usage_rows(
                        ledger,
                        user_id=owner_id,
                        project_id=project_id,
                        operation="chat_title",
                        run_id=new_run_id(),
                    )
                )
            except Exception as error:  # noqa: BLE001 - accounting must never fail naming
                logger.warning("Could not record title usage: %s", error)
        await session.refresh(project)
        if title and project.title == provisional:
            await update_quietly(session, project, title=title)
        await session.commit()


@app.post("/projects", status_code=201)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    background: BackgroundTasks = None,  # type: ignore[assignment]  # injected by FastAPI
):
    if payload.folder_id is not None:
        await _owned_folder(session, payload.folder_id)
    owner_id = _context_user_id()
    account = await session.get(UserAccount, owner_id) if owner_id is not None else None
    workspace = None
    if account is None:
        workspace = await session.scalar(
            select(WorkspaceProfile).where(WorkspaceProfile.profile_key == "local")
        )
    default_text_language = (
        account.default_text_language
        if account
        else workspace.default_text_language
        if workspace
        else "English"
    )
    default_voice_language = (
        account.default_voice_language
        if account
        else workspace.default_voice_language
        if workspace
        else "English"
    )
    default_duration_minutes = (
        account.default_duration_minutes
        if account
        else workspace.default_duration_minutes
        if workspace
        else DEFAULT_VIDEO_DURATION_MINUTES
    )
    text_language = payload.text_language or payload.language or default_text_language
    voice_language = payload.voice_language or payload.language or default_voice_language
    # A length the learner states in the chat is the planner's decision at that turn.
    duration_minutes = payload.target_duration_minutes or default_duration_minutes
    canonical_request = await TranslationService(
        create_translation_provider(settings), create_machine_translator(settings)
    ).to_english(
        # The translation model detects the language the learner typed in; the UI's
        # selected output language says nothing about the input.
        payload.request,
    )
    project = Project(
        owner_id=owner_id,
        folder_id=payload.folder_id,
        title=payload.title,
        request=canonical_request,
        original_request=payload.request,
        target_duration_minutes=duration_minutes,
        mode=payload.mode,
        # Agents consume canonical English regardless of presentation language.
        language="English",
        text_language=text_language,
        voice_language=voice_language,
    )
    session.add(project)
    await session.flush()
    store.assign_project(project.id, project.owner_id)
    session.add(
        ProjectRevision(
            project_id=project.id,
            version=1,
            reason="created",
            payload={
                **payload.model_dump(mode="json"),
                "resolved_target_duration_minutes": duration_minutes,
                "canonical_request": canonical_request,
                "canonical_language": "English",
                "text_language": text_language,
                "voice_language": voice_language,
            },
            active=True,
        )
    )
    await session.commit()
    await session.refresh(project)
    if background is not None:
        background.add_task(name_chat, project.id, owner_id, project.title)
    return _project(project)


@app.get("/projects")
async def list_projects(
    session: SessionDep,
    query: str | None = Query(default=None, max_length=200),
    archived: bool = Query(default=False),
):
    """List durable conversations, optionally searching their lesson prompt."""
    statement = (
        select(Project)
        .where(Project.owner_id == _context_user_id(), Project.archived.is_(archived))
        .order_by(Project.pinned.desc(), Project.updated_at.desc(), Project.created_at.desc())
    )
    if query and (term := query.strip()):
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Project.title.ilike(pattern),
                Project.request.ilike(pattern),
                Project.original_request.ilike(pattern),
                exists(
                    select(TutorMessage.id).where(
                        TutorMessage.project_id == Project.id,
                        or_(
                            TutorMessage.display_text.ilike(pattern),
                            TutorMessage.canonical_text.ilike(pattern),
                        ),
                    )
                ),
            )
        )
    return [_project(item) for item in (await session.scalars(statement)).all()]


@app.get("/projects/{project_id}")
async def get_project(project_id: UUID, session: SessionDep):
    project = await _owned_project(session, project_id)
    return _project(project)


@app.patch("/v1/projects/{project_id}")
async def update_project(project_id: UUID, payload: ProjectUpdate, session: SessionDep):
    project = await _owned_project(session, project_id)
    values = payload.model_dump(exclude_unset=True)
    if "title" in values:
        project.title = values["title"].strip()
    if "pinned" in values:
        project.pinned = values["pinned"]
    if "archived" in values:
        project.archived = values["archived"]
    project.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(project)
    return _project(project)


@app.delete("/v1/projects/{project_id}", status_code=204)
async def delete_project(project_id: UUID, session: SessionDep):
    project = await _owned_project(session, project_id)
    await session.delete(project)
    await session.commit()


@app.post("/v1/projects/{project_id}/move")
async def move_project(project_id: UUID, payload: ProjectMoveRequest, session: SessionDep):
    project = await _owned_project(session, project_id)
    if payload.folder_id is not None:
        folder = await _owned_folder(session, payload.folder_id)
        folder.updated_at = datetime.now(UTC)
    project.folder_id = payload.folder_id
    project.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(project)
    return _project(project)


@app.post("/v1/projects/{project_id}/copy", status_code=201)
async def copy_project(project_id: UUID, payload: ProjectCopyRequest, session: SessionDep):
    source = await _owned_project(session, project_id)
    if payload.folder_id is not None:
        folder = await _owned_folder(session, payload.folder_id)
        folder.updated_at = datetime.now(UTC)
    copied = Project(
        owner_id=_context_user_id(),
        folder_id=payload.folder_id,
        title=(payload.title or f"{source.title} copy").strip(),
        request=source.request,
        original_request=source.original_request,
        target_duration_minutes=source.target_duration_minutes,
        mode=source.mode,
        language=source.language,
        text_language=source.text_language,
        voice_language=source.voice_language,
        status="CREATED",
    )
    session.add(copied)
    await session.flush()
    store.assign_project(copied.id, copied.owner_id)
    session.add(
        ProjectRevision(
            project_id=copied.id,
            version=1,
            reason=f"copied from {source.id}",
            payload={"source_project_id": str(source.id)},
            active=True,
        )
    )
    source_messages = (
        await session.scalars(
            select(TutorMessage)
            .where(TutorMessage.project_id == source.id)
            .order_by(TutorMessage.created_at)
        )
    ).all()
    for message in source_messages:
        session.add(
            TutorMessage(
                project_id=copied.id,
                role=message.role,
                canonical_text=message.canonical_text,
                display_text=message.display_text,
                language=message.language,
                action=message.action,
                metadata_json=dict(message.metadata_json or {}),
            )
        )
    await session.commit()
    await session.refresh(copied)
    return _project(copied)


@app.patch("/projects/{project_id}/settings")
async def update_project_settings(
    project_id: UUID, payload: ProjectSettingsUpdate, session: SessionDep
):
    """Persist lesson preferences without adding a synthetic chat message."""
    project = await _owned_project(session, project_id)
    changed_languages = any(
        getattr(project, field) != value
        for field, value in payload.model_dump(exclude_none=True).items()
        if field in {"text_language", "voice_language"}
    )
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(project, field, value.strip() if isinstance(value, str) else value)

    workflow_id = run_id = None
    if changed_languages and project.status == "COMPLETE":
        try:
            workflow_id, run_id = await start_localize_workflow(settings, project_id)
        except Exception as error:
            raise HTTPException(503, f"Temporal workflow start failed: {error}") from error
        project.status = "LOCALIZING"
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type="LocalizeProjectWorkflow",
                status="RUNNING",
                detail={"source": "project_settings"},
            )
        )
    await session.commit()
    await session.refresh(project)
    return {**_project(project), "workflow_id": workflow_id, "run_id": run_id}


@app.post("/projects/{project_id}/scenes/{scene_id}/localize", status_code=202)
async def localize_scene(
    project_id: UUID,
    scene_id: str,
    payload: ProjectSettingsUpdate,
    session: SessionDep,
):
    """Translate and re-render one scene, preserving all other active scenes."""
    project = await _owned_project(session, project_id)
    if project.status != "COMPLETE":
        raise HTTPException(409, "complete the video before localizing a scene")
    if payload.text_language is None and payload.voice_language is None:
        raise HTTPException(422, "provide text_language, voice_language, or both")
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if scene is None:
        raise HTTPException(404, "scene not found")
    if scene.timeline_start is None or scene.timeline_end is None:
        raise HTTPException(409, "scene has no active video timeline")
    if payload.text_language:
        project.text_language = payload.text_language.strip()
    if payload.voice_language:
        project.voice_language = payload.voice_language.strip()
    try:
        workflow_id, run_id = await start_localize_workflow(settings, project_id, scene_id)
    except Exception as error:
        raise HTTPException(503, f"Temporal workflow start failed: {error}") from error
    project.status = "LOCALIZING"
    session.add(
        WorkflowExecution(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="LocalizeProjectWorkflow",
            status="RUNNING",
            detail={"source": "scene_localization", "scene_id": scene_id},
        )
    )
    await session.commit()
    return {
        "status": "queued",
        "scene_id": scene_id,
        "text_language": project.text_language,
        "voice_language": project.voice_language,
        "workflow_id": workflow_id,
        "run_id": run_id,
    }


@app.get("/projects/{project_id}/memory")
async def project_memory(project_id: UUID, session: SessionDep):
    """Return a compact, inspectable summary of a tutor chat's durable context."""
    project = await _owned_project(session, project_id)
    message_count = await session.scalar(
        select(func.count(TutorMessage.id)).where(TutorMessage.project_id == project_id)
    )
    source_count = await session.scalar(
        select(func.count(SourceDocument.id)).where(SourceDocument.project_id == project_id)
    )
    scene_count = await session.scalar(
        select(func.count(Scene.id)).where(Scene.project_id == project_id)
    )
    revision_count = await session.scalar(
        select(func.count(ProjectRevision.id)).where(ProjectRevision.project_id == project_id)
    )
    correction_count = await session.scalar(
        select(func.count(CorrectionEntry.id)).where(CorrectionEntry.status == "active")
    )
    skill_count = await session.scalar(select(func.count(Skill.id)).where(Skill.status == "active"))
    return {
        "conversation": {
            "message_count": message_count or 0,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        },
        "lesson": {
            "canonical_request": project.request,
            "original_request": project.original_request,
            "mode": project.mode,
            "text_language": project.text_language,
            "voice_language": project.voice_language,
        },
        "artifacts": {
            "sources": source_count or 0,
            "scenes": scene_count or 0,
            "revisions": revision_count or 0,
            "has_video": bool(project.active_video_path),
        },
        "agent_memory": {
            "active_corrections": correction_count or 0,
            "active_skills": skill_count or 0,
        },
    }


@app.get("/projects/{project_id}/revisions")
async def project_revisions(project_id: UUID, session: SessionDep):
    await _owned_project(session, project_id)
    values = (
        await session.scalars(
            select(ProjectRevision)
            .where(ProjectRevision.project_id == project_id)
            .order_by(ProjectRevision.version.desc())
        )
    ).all()
    return [
        {
            "version": item.version,
            "reason": item.reason,
            "payload": item.payload,
            "active": item.active,
            "created_at": item.created_at,
        }
        for item in values
    ]


@app.post("/projects/{project_id}/cancel", status_code=202)
async def cancel_project(project_id: UUID, session: SessionDep):
    project = await _owned_project(session, project_id)
    execution = await session.scalar(
        select(WorkflowExecution)
        .where(WorkflowExecution.project_id == project_id, WorkflowExecution.status == "RUNNING")
        .order_by(WorkflowExecution.created_at.desc())
    )
    if execution is None:
        raise HTTPException(409, "no running workflow")
    try:
        await cancel_workflow(settings, execution.workflow_id)
    except Exception as error:
        raise HTTPException(503, f"Temporal cancellation failed: {error}") from error
    execution.status = "CANCELLING"
    project.status = "CANCELLING"
    await session.commit()
    return {
        "project_id": str(project_id),
        "workflow_id": execution.workflow_id,
        "status": "cancelling",
    }


@app.post("/projects/{project_id}/sources", status_code=201)
async def upload_source(
    project_id: UUID,
    file: SourceFile,
    session: SessionDep,
):
    await _owned_project(session, project_id)
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "source exceeds 100 MB")
    key = f"{project_id}/sources/{file.filename or 'source.pdf'}"
    if await session.scalar(
        select(SourceDocument.id).where(
            SourceDocument.project_id == project_id, SourceDocument.uri == key
        )
    ):
        raise HTTPException(409, "a source with this name already exists")
    # A file without a source record was left by an upload that failed; replace it.
    store.put_bytes(key, data, overwrite=True)
    document = SourceDocument(
        project_id=project_id,
        kind="paper",
        title=Path(file.filename or "source").stem,
        uri=key,
        metadata_json={"content_type": file.content_type},
    )
    session.add(document)
    await session.flush()
    if (file.filename or "").lower().endswith(".pdf"):
        await index_source(
            session, settings, document, parse_stored_pdf(store, key, project_id, document.id)
        )
    await session.commit()
    await session.refresh(document)
    return {"id": str(document.id), "title": document.title, "uri": document.uri}


@app.post("/projects/{project_id}/sources/link", status_code=201)
async def import_source_link(
    project_id: UUID,
    payload: SourceLinkRequest,
    session: SessionDep,
):
    await _owned_project(session, project_id)
    try:
        document = await ingest_link(session, settings, store, project_id, payload.url)
    except LinkUnreadable as error:
        raise HTTPException(422, f"the link could not be read: {error.reason}") from error
    await session.commit()
    return {"id": str(document.id), "title": document.title, "uri": document.uri}


@app.post("/projects/{project_id}/generate", status_code=202)
async def generate(
    project_id: UUID,
    session: SessionDep,
):
    project = await _owned_project(session, project_id)
    if project.status == "GENERATING":
        raise HTTPException(409, "generation already running")
    await reset_failed_generation(session, project)
    try:
        workflow_id, run_id = await start_build_workflow(settings, project_id)
    except Exception as error:
        raise HTTPException(503, f"Temporal workflow start failed: {error}") from error
    # A learner can begin with a normal tutor chat and later opt into a video
    # without losing the conversation that established their context.
    if project.mode == "chat":
        project.mode = "both"
    project.status = "GENERATING"
    session.add(
        WorkflowExecution(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="BuildVideoWorkflow",
            status="RUNNING",
        )
    )
    await session.commit()
    return {
        "project_id": str(project_id),
        "status": "queued",
        "workflow_id": workflow_id,
        "run_id": run_id,
    }


@app.get("/projects/{project_id}/progress")
async def progress(project_id: UUID, session: SessionDep):
    project = await _owned_project(session, project_id)
    scene_count = await session.scalar(
        select(func.count(Scene.id)).where(Scene.project_id == project_id)
    )
    events = (
        await session.scalars(
            select(ProgressEvent)
            .where(ProgressEvent.project_id == project_id)
            .order_by(ProgressEvent.created_at)
        )
    ).all()
    latest_execution = await session.scalar(
        select(WorkflowExecution)
        .where(WorkflowExecution.project_id == project_id)
        .order_by(WorkflowExecution.created_at.desc())
    )
    if (
        project.status in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING"}
        and latest_execution is not None
        and latest_execution.status in {"RUNNING", "CANCELLING", "FAILED", "COMPLETED"}
    ):
        try:
            temporal_status = await get_workflow_status(settings, latest_execution.workflow_id)
        except Exception:  # noqa: BLE001 - progress polling must survive a Temporal outage
            temporal_status = None
        status_name = getattr(temporal_status, "name", None)
        if status_name == "CANCELED":
            project.status = "CANCELLED"
            latest_execution.status = "CANCELLED"
            await session.commit()
        elif status_name == "FAILED":
            project.status = "FAILED"
            latest_execution.status = "FAILED"
            latest_execution.detail = {
                **(latest_execution.detail or {}),
                "error": (latest_execution.detail or {}).get("error")
                or "The background workflow failed before it could publish a completed result.",
            }
            await session.commit()
        elif status_name == "COMPLETED":
            video_exists = bool(
                project.active_video_path and Path(project.active_video_path).is_file()
            )
            project.status = "COMPLETE" if video_exists else "FAILED"
            latest_execution.status = "COMPLETED" if video_exists else "FAILED"
            if not video_exists:
                latest_execution.detail = {
                    **(latest_execution.detail or {}),
                    "error": "Workflow completed without an active video artifact.",
                }
            await session.commit()
    failure_message = None
    if project.status == "FAILED":
        error = str((latest_execution.detail or {}).get("error") or "").casefold()
        if "every scene was blocked" in error:
            failure_message = (
                "The scene checks found layout or narration timing problems, so no video was saved. "
                "You can retry with a simpler lesson prompt."
            )
        else:
            failure_message = (
                "The video build stopped before it could save a finished video. You can retry."
            )
    return {
        "status": project.status,
        "failure_message": failure_message,
        "scene_count": scene_count or 0,
        "events": [
            {
                "event": event.event,
                "scene_id": str(event.scene_id) if event.scene_id else None,
                "detail": event.detail,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


@app.get("/projects/{project_id}/chat")
async def tutor_messages(project_id: UUID, session: SessionDep):
    """The active conversation; an edited turn carries its older branches for ‹ › browsing."""
    await _owned_project(session, project_id)
    everything = await chat_history(session, project_id, include_superseded=True)

    def payload(message: TutorMessage) -> dict:
        return {
            "id": str(message.id),
            "role": message.role,
            "message": message.display_text,
            "canonical_message": message.canonical_text,
            "language": message.language,
            "action": message.action,
            "metadata": message.metadata_json,
            "created_at": message.created_at,
        }

    branches: dict[str, dict[int, list[dict]]] = {}
    for message in everything:
        metadata = message.metadata_json or {}
        if metadata.get("superseded"):
            branches.setdefault(metadata["branch_group"], {}).setdefault(
                metadata["branch_index"], []
            ).append(payload(message))
    active = []
    for message in everything:
        if (message.metadata_json or {}).get("superseded"):
            continue
        item = payload(message)
        group = (message.metadata_json or {}).get("branch_group")
        if message.role == "user" and group in branches:
            item["branches"] = [
                {"index": index, "messages": branches[group][index]}
                for index in sorted(branches[group])
            ]
            item["branch_index"] = (message.metadata_json or {}).get("branch_index")
        active.append(item)
    return active


async def _usage_rows_for(ledger, *, project_id: UUID | None, operation: str) -> list:
    """Usage rows for one chat turn, priced where the provider reported no cost."""
    if not ledger.entries:
        return []
    try:
        if any(entry.cost_usd is None for entry in ledger.entries):
            ledger.estimate_missing_costs(await openrouter_prices(settings))
        return usage_rows(
            ledger,
            user_id=current_user_id.get(),
            project_id=project_id,
            operation=operation,
            run_id=new_run_id(),
        )
    except Exception as error:  # noqa: BLE001 - accounting must never fail a chat turn
        logger.warning("Could not record chat usage: %s", error)
        return []


async def _respond_to_chat(
    project_id: UUID,
    project: Project,
    payload: TutorChatRequest,
    session: AsyncSession,
) -> TutorChatResponse:
    with track_usage() as ledger:
        decision = await tutor_respond(session, settings, project, payload)
    # Saved with this turn's messages in the commit below.
    session.add_all(await _usage_rows_for(ledger, project_id=project_id, operation="chat"))
    workflow_id = run_id = None
    if decision.base_version is not None:
        # The learner meant an earlier video: make it current before changing it.
        requested = (project.text_language, project.voice_language)
        if not await activate_video_version(session, project, decision.base_version):
            raise HTTPException(409, "that earlier video can no longer be restored")
        if decision.action in {"localize", "localize_scene"}:
            project.text_language, project.voice_language = requested
        await session.commit()
    if decision.action == "edit_range":
        workflow_id, run_id = await start_edit_workflow(
            settings,
            project_id,
            decision.start or 0,
            decision.end or 0,
            decision.instruction or decision.canonical_message,
        )
        project.status = "EDITING"
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type="EditProjectWorkflow",
                status="RUNNING",
                detail={"source": "tutor_chat", "range": [decision.start, decision.end]},
            )
        )
    elif decision.action == "generate_video":
        try:
            workflow_id, run_id = await start_build_workflow(settings, project_id)
        except Exception as error:
            raise HTTPException(503, f"Temporal workflow start failed: {error}") from error
        project.status = "GENERATING"
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type="BuildVideoWorkflow",
                status="RUNNING",
                detail={"source": "tutor_chat"},
            )
        )
    elif decision.action == "localize_scene":
        # Only the named scene is translated and re-rendered; the rest of the video stays.
        workflow_id, run_id = await start_localize_workflow(
            settings, project_id, decision.instruction
        )
        project.status = "LOCALIZING"
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type="LocalizeProjectWorkflow",
                status="RUNNING",
                detail={"source": "tutor_chat", "scene_id": decision.instruction},
            )
        )
    elif decision.action == "localize":
        workflow_id, run_id = await start_localize_workflow(settings, project_id)
        project.status = "LOCALIZING"
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type="LocalizeProjectWorkflow",
                status="RUNNING",
                detail={"source": "tutor_chat"},
            )
        )
    await session.commit()
    return TutorChatResponse(
        message=decision.message,
        action=decision.action,
        canonical_message=decision.canonical_message,
        range_start=decision.start,
        range_end=decision.end,
        workflow_id=workflow_id,
        run_id=run_id,
    )


@app.post("/projects/{project_id}/chat", response_model=TutorChatResponse)
async def tutor_chat(
    project_id: UUID,
    payload: TutorChatRequest,
    session: SessionDep,
    background: BackgroundTasks = None,  # type: ignore[assignment]  # injected by FastAPI
):
    project = await _owned_project(session, project_id)
    response = await _respond_to_chat(project_id, project, payload, session)
    if background is not None:
        background.add_task(compact_chat, project_id, current_user_id.get())
    return response


async def compact_chat(project_id: UUID, owner_id: UUID | None) -> None:
    """Fold older turns into the chat's running summary once they are long enough."""
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return
        owner = await session.get(UserAccount, owner_id) if owner_id else None
        with track_usage() as ledger:
            changed = await compact(
                session,
                create_planner_provider(settings),
                project,
                await chat_history(session, project_id),
                research.resolve(owner.research_settings if owner else None).chat_turns_kept,
            )
        if ledger.entries:
            try:
                if any(entry.cost_usd is None for entry in ledger.entries):
                    ledger.estimate_missing_costs(await openrouter_prices(settings))
                session.add_all(
                    usage_rows(
                        ledger,
                        user_id=owner_id,
                        project_id=project_id,
                        operation="chat_summary",
                        run_id=new_run_id(),
                    )
                )
            except Exception as error:  # noqa: BLE001 - accounting must never fail compaction
                logger.warning("Could not record summary usage: %s", error)
        if changed or ledger.entries:
            await session.commit()


@app.post("/v1/temporary-chat", response_model=TutorChatResponse)
async def temporary_chat(payload: TemporaryChatRequest):
    with track_usage() as ledger:
        message = await respond_ephemeral(settings, payload, payload.history)
    rows = await _usage_rows_for(ledger, project_id=None, operation="temporary_chat")
    if rows:
        async with database.sessions() as session:
            session.add_all(rows)
            await session.commit()
    return TutorChatResponse(message=message, canonical_message=message)


@app.patch("/projects/{project_id}/chat/{message_id}", response_model=TutorChatResponse)
async def edit_tutor_message(
    project_id: UUID,
    message_id: UUID,
    payload: TutorMessageEdit,
    session: SessionDep,
):
    """Revise a learner message, discard its stale continuation, and answer again."""
    project = await _owned_project(session, project_id)
    if project.status in {"GENERATING", "EDITING", "LOCALIZING", "CANCELLING"}:
        raise HTTPException(409, "Wait for the active lesson operation before editing a message")

    history = await chat_history(session, project_id)
    message_index = next(
        (index for index, item in enumerate(history) if item.id == message_id), None
    )
    if message_index is None or history[message_index].role != "user":
        raise HTTPException(404, "learner message not found")

    edited_text = payload.message.strip()
    if not edited_text:
        raise HTTPException(422, "message cannot be empty")
    original_language = history[message_index].language
    # Keep the replaced turn and its continuation as a read-only older branch,
    # like ChatGPT/Claude; the conversation continues from the edited text.
    edited = history[message_index]
    group = (edited.metadata_json or {}).get("branch_group") or str(edited.id)
    old_index = (edited.metadata_json or {}).get("branch_index", 1)
    everything = await chat_history(session, project_id, include_superseded=True)
    new_index = 1 + max(
        [old_index]
        + [
            (item.metadata_json or {}).get("branch_index", 1)
            for item in everything
            if (item.metadata_json or {}).get("branch_group") == group
        ]
    )
    for item in history[message_index:]:
        metadata = dict(item.metadata_json or {})
        metadata.update(superseded=True, branch_group=group, branch_index=old_index)
        item.metadata_json = metadata
    # A summary that covers the replaced turn is rebuilt from the new branch.
    forget_from(project, edited.created_at)
    await session.flush()

    # Keep the canonical project brief aligned when the first learner turn is
    # revised in a text-only conversation. Completed video artifacts remain
    # versioned independently from this conversational edit.
    if message_index == 0 and not project.active_video_path:
        project.original_request = edited_text
        project.request = await TranslationService(
            create_translation_provider(settings), create_machine_translator(settings)
        ).to_english(edited_text, source_language=original_language)

    response = await _respond_to_chat(
        project_id,
        project,
        TutorChatRequest(
            message=edited_text,
            language=original_language,
            text_language=project.text_language,
            voice_language=project.voice_language,
        ),
        session,
    )
    newest = next(
        (m for m in reversed(await chat_history(session, project_id)) if m.role == "user"), None
    )
    if newest is not None:
        newest.metadata_json = {
            **(newest.metadata_json or {}),
            "branch_group": group,
            "branch_index": new_index,
        }
        await session.commit()
    return response


@app.get("/projects/{project_id}/scenes")
async def scenes(project_id: UUID, session: SessionDep):
    await _owned_project(session, project_id)
    values = (
        await session.scalars(
            # The current video's scenes; ones only an earlier version used have no span.
            select(Scene)
            .where(Scene.project_id == project_id, Scene.timeline_start.is_not(None))
            .order_by(Scene.order_index)
        )
    ).all()
    return [_scene(item) for item in values]


def _video_file(project: Project, version: int | None, name: str) -> Path | None:
    """A file of the active video, or of an earlier kept version (``video/v<N>/``)."""
    if not project.active_video_path:
        return None
    folder = Path(project.active_video_path).parent
    if version is not None:
        folder = folder.parent / f"v{version}"
    candidate = folder / name
    return candidate if candidate.is_file() else None


@app.get("/projects/{project_id}/videos")
async def video_versions(project_id: UUID, session: SessionDep):
    """Every kept video version, oldest first, so a chat can show each edit's result."""
    project = await _owned_project(session, project_id)
    if not project.active_video_path:
        return []
    root = Path(project.active_video_path).parent.parent
    versions = []
    for folder in root.glob("v*"):
        final = folder / "final.mp4"
        if folder.name[1:].isdigit() and final.is_file():
            created = datetime.fromtimestamp(final.stat().st_mtime, UTC)
            versions.append({"version": int(folder.name[1:]), "created_at": created})
    return sorted(versions, key=lambda item: item["version"])


@app.get("/projects/{project_id}/video")
async def video(
    project_id: UUID, session: SessionDep, version: int | None = Query(default=None, ge=1)
):
    project = await _owned_project(session, project_id)
    final = _video_file(project, version, "final.mp4")
    if final is None:
        raise HTTPException(404, "video not ready")
    return FileResponse(final, media_type="video/mp4", filename=f"{project.title}.mp4")


@app.get("/projects/{project_id}/subtitles.vtt")
async def subtitles_vtt(
    project_id: UUID, session: SessionDep, version: int | None = Query(default=None, ge=1)
):
    """Serve a video's narration captions as a browser subtitle track."""
    project = await _owned_project(session, project_id)
    subtitle_path = _video_file(project, version, "subtitles.vtt")
    if subtitle_path is None:
        raise HTTPException(404, "subtitles not ready")
    return FileResponse(
        subtitle_path,
        media_type="text/vtt",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/projects/{project_id}/timeline")
async def timeline(
    project_id: UUID, session: SessionDep, version: int | None = Query(default=None, ge=1)
):
    await _owned_project(session, project_id)
    version = version or await session.scalar(
        select(func.max(TimelineSegment.video_version)).where(
            TimelineSegment.project_id == project_id
        )
    )
    values = (
        await session.scalars(
            select(TimelineSegment)
            .where(
                TimelineSegment.project_id == project_id, TimelineSegment.video_version == version
            )
            .order_by(TimelineSegment.start_seconds)
        )
    ).all()
    return {
        "video_version": version,
        "scenes": [
            {
                "scene_id": str(item.scene_id),
                "start": item.start_seconds,
                "end": item.end_seconds,
                "render_version": item.render_version,
            }
            for item in values
        ],
    }


@app.get("/projects/{project_id}/chapters.vtt", response_class=PlainTextResponse)
async def chapters_vtt(
    project_id: UUID, session: SessionDep, version: int | None = Query(default=None, ge=1)
):
    """Browser-native chapter metadata for the video controller."""
    await _owned_project(session, project_id)
    version = version or await session.scalar(
        select(func.max(TimelineSegment.video_version)).where(
            TimelineSegment.project_id == project_id
        )
    )
    segments = (
        await session.execute(
            select(TimelineSegment, Scene)
            .join(Scene, Scene.id == TimelineSegment.scene_id)
            .where(
                TimelineSegment.project_id == project_id, TimelineSegment.video_version == version
            )
            .order_by(TimelineSegment.start_seconds)
        )
    ).all()

    def timestamp(value: float) -> str:
        hours, remainder = divmod(value, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"

    cues = ["WEBVTT", ""]
    for index, (segment, scene) in enumerate(segments, 1):
        cues.extend(
            [
                str(index),
                f"{timestamp(segment.start_seconds)} --> {timestamp(segment.end_seconds)}",
                scene.title.replace("\n", " "),
                "",
            ]
        )
    return PlainTextResponse("\n".join(cues), media_type="text/vtt")


@app.post("/projects/{project_id}/feedback", status_code=202)
async def feedback(project_id: UUID, payload: dict, session: SessionDep):
    """Free-text feedback on a finished video; the planner decides which part it means."""
    project = await _owned_project(session, project_id)
    if project.status != "COMPLETE":
        raise HTTPException(409, "complete the video before sending feedback")
    message = str(payload.get("message", ""))
    scenes = await video_scenes(session, project)
    preparation = await prepare_lesson(
        create_llm_provider(settings),
        message=message,
        project_request=project.request,
        conversation="",
        account_text_language=project.text_language,
        account_voice_language=project.voice_language,
        project_text_language=project.text_language,
        project_voice_language=project.voice_language,
        mode=project.mode,
        video_scenes=planner_scenes(scenes),
    )
    span = edit_range_for(preparation, scenes) if preparation.action == "edit_video" else None
    if span is None:
        raise HTTPException(422, "could not tell which part of the video to change")
    instruction = preparation.edit_instruction or preparation.canonical_message or message
    workflow_id, run_id = await start_edit_workflow(
        settings, project_id, span[0], span[1], instruction
    )
    session.add(
        WorkflowExecution(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="EditProjectWorkflow",
            status="RUNNING",
        )
    )
    await session.commit()
    return {
        "start_seconds": span[0],
        "end_seconds": span[1],
        "instruction": instruction,
        "workflow_id": workflow_id,
        "run_id": run_id,
    }


@app.post("/projects/{project_id}/edit-range", status_code=202)
async def edit_range(project_id: UUID, payload: EditRangeRequest, session: SessionDep):
    project = await _owned_project(session, project_id)
    workflow_id, run_id = await start_edit_workflow(
        settings, project_id, payload.start_time, payload.end_time, payload.instruction
    )
    project.status = "EDITING"
    session.add(
        WorkflowExecution(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="EditProjectWorkflow",
            status="RUNNING",
        )
    )
    await session.commit()
    return {
        "status": "queued",
        "range": [payload.start_time, payload.end_time],
        "workflow_id": workflow_id,
        "run_id": run_id,
    }


@app.post("/projects/{project_id}/scenes/{scene_id}/edit", status_code=202)
async def edit_scene(
    project_id: UUID,
    scene_id: str,
    payload: dict,
    session: SessionDep,
):
    project = await _owned_project(session, project_id)
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    workflow_id, run_id = await start_edit_workflow(
        settings,
        project_id,
        scene.timeline_start or 0,
        scene.timeline_end or scene.duration,
        payload.get("instruction", "regenerate scene"),
    )
    project.status = "EDITING"
    session.add(
        WorkflowExecution(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="EditProjectWorkflow",
            status="RUNNING",
        )
    )
    await session.commit()
    return {"status": "queued", "scene_id": scene_id, "workflow_id": workflow_id, "run_id": run_id}


@app.post("/projects/{project_id}/scenes/{scene_id}/regenerate", status_code=202)
async def regenerate_scene(
    project_id: UUID,
    scene_id: str,
    session: SessionDep,
):
    return await edit_scene(
        project_id,
        scene_id,
        {"instruction": "Regenerate this scene while preserving its content."},
        session,
    )


@app.get("/projects/{project_id}/scenes/{scene_id}/versions")
async def versions(project_id: UUID, scene_id: str, session: SessionDep):
    await _owned_project(session, project_id)
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    values = (
        await session.scalars(
            select(SceneVersion)
            .where(SceneVersion.scene_id == scene.id)
            .order_by(SceneVersion.version.desc())
        )
    ).all()
    return [
        {
            "version": item.version,
            "reason": item.reason,
            "active": item.active,
            "snapshot": item.snapshot,
            "created_at": item.created_at,
        }
        for item in values
    ]


@app.post("/projects/{project_id}/scenes/{scene_id}/restore/{version}")
async def restore_version(
    project_id: UUID,
    scene_id: str,
    version: int,
    session: SessionDep,
):
    await _owned_project(session, project_id)
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    target = await session.scalar(
        select(SceneVersion).where(
            SceneVersion.scene_id == scene.id, SceneVersion.version == version
        )
    )
    if not target:
        raise HTTPException(404, "version not found")
    for item in (
        await session.scalars(select(SceneVersion).where(SceneVersion.scene_id == scene.id))
    ).all():
        item.active = item.id == target.id
    snapshot = target.snapshot
    scene.active_spec_version = snapshot["spec_version"]
    scene.active_code_version = snapshot["code_version"]
    scene.active_audio_version = snapshot["audio_version"]
    scene.active_render_version = snapshot["render_version"]
    versioned_models = (
        (SceneSpecification, snapshot["spec_version"]),
        (GeneratedCode, snapshot["code_version"]),
        (AudioArtifact, snapshot["audio_version"]),
        (RenderArtifact, snapshot["render_version"]),
    )
    for model, active_version in versioned_models:
        for artifact in (
            await session.scalars(select(model).where(model.scene_id == scene.id))
        ).all():
            artifact.active = artifact.version == active_version
            artifact.stale = artifact.version != active_version
    await session.commit()
    video_version = await reassemble_active(database, settings, project_id)
    return {
        "restored": version,
        "scene_id": scene_id,
        "video_version": video_version,
        "reassembly_required": False,
    }


@app.get("/corpus/health")
async def corpus_health():
    """Report Qdrant reachability; PostgreSQL remains authoritative."""
    try:
        await QdrantVectorStore(QdrantConfig.from_settings(settings)).ensure_collections()
        return {"enabled": True, "reachable": True, "platform": "qdrant"}
    except QdrantUnavailable as error:
        return {"enabled": True, "reachable": False, "platform": "qdrant", "error": str(error)}


@app.get("/corrections")
async def corrections(session: SessionDep):
    values = (
        await session.scalars(select(CorrectionEntry).order_by(CorrectionEntry.confidence.desc()))
    ).all()
    return [
        {
            "id": item.correction_key,
            "category": item.category,
            "confidence": item.confidence,
            "evidence_count": item.evidence_count,
        }
        for item in values
    ]


@app.get("/corrections/{correction_id}")
async def get_correction(correction_id: str, session: SessionDep):
    item = await session.scalar(
        select(CorrectionEntry).where(CorrectionEntry.correction_key == correction_id)
    )
    if item is None:
        raise HTTPException(404, "correction not found")
    return {
        "id": item.correction_key,
        "category": item.category,
        "trigger_conditions": item.trigger_conditions,
        "anti_pattern": item.anti_pattern,
        "required_behavior": item.required_behavior,
        "recommended_fix": item.recommended_fix,
        "evidence_count": item.evidence_count,
        "confidence": item.confidence,
        "tags": item.tags,
        "status": item.status,
        "validation_tests": item.validation_tests,
    }


@app.get("/correction-candidates")
async def correction_candidates(session: SessionDep):
    values = (
        await session.scalars(
            select(CorrectionCandidate)
            .where(CorrectionCandidate.status == "candidate")
            .order_by(CorrectionCandidate.confidence.desc())
        )
    ).all()
    return [
        {
            "id": str(item.id),
            "category": item.category,
            "trigger_conditions": item.trigger_conditions,
            "recommended_fix": item.recommended_fix,
            "evidence_count": item.evidence_count,
            "confidence": item.confidence,
            "evidence": item.evidence,
        }
        for item in values
    ]


@app.post("/correction-candidates/{candidate_id}/approve")
async def approve_correction_candidate(
    candidate_id: UUID,
    session: SessionDep,
    admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    require_admin(settings, admin_key)
    candidate = await session.get(CorrectionCandidate, candidate_id)
    if candidate is None or candidate.status != "candidate":
        raise HTTPException(404, "correction candidate not found")
    if candidate.evidence_count < 2 or candidate.confidence < 0.8:
        raise HTTPException(409, "candidate has not met the evidence threshold")
    correction = CorrectionEntry(
        correction_key=f"CORR-APPROVED-{UUID(int=time.time_ns() % (1 << 128)).hex[:12].upper()}",
        category=candidate.category,
        trigger_conditions=candidate.trigger_conditions,
        anti_pattern=f"Repeat the observed {candidate.category} failure.",
        required_behavior=f"Prevent {candidate.category} while preserving the scene contract.",
        recommended_fix=candidate.recommended_fix,
        evidence_count=candidate.evidence_count,
        confidence=candidate.confidence,
        tags=[candidate.category],
        applicable_model="any",
        status="active",
        validation_tests=["administrator_reviewed_candidate"],
    )
    session.add(correction)
    candidate.status = "promoted"
    session.add(
        AdministrationAudit(
            action="correction.approve",
            target_type="correction_candidate",
            target_id=str(candidate.id),
            detail={"correction_key": correction.correction_key, "category": candidate.category},
        )
    )
    await session.commit()
    return {"id": correction.correction_key, "status": correction.status}


@app.post("/corrections/rebuild-index")
async def rebuild_correction_index(
    session: SessionDep,
    admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    require_admin(settings, admin_key)
    try:
        counts = await reconcile_qdrant_index(session, settings)
        await session.commit()
        return {"status": "complete", **counts}
    except QdrantUnavailable as error:
        await session.rollback()
        raise HTTPException(503, str(error)) from error


@app.post("/corrections/{correction_id}/disable")
async def disable_correction(
    correction_id: str,
    session: SessionDep,
    admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    require_admin(settings, admin_key)
    item = await session.scalar(
        select(CorrectionEntry).where(CorrectionEntry.correction_key == correction_id)
    )
    if item is None:
        raise HTTPException(404, "correction not found")
    item.status = "disabled"
    session.add(
        AdministrationAudit(
            action="correction.disable",
            target_type="correction",
            target_id=item.correction_key,
            detail={"category": item.category},
        )
    )
    await session.commit()
    return {"id": item.correction_key, "status": item.status}


@app.get("/admin/audit")
async def administration_audit(
    session: SessionDep,
    admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
):
    """List privileged changes without ever exposing the admin credential."""
    require_admin(settings, admin_key)
    entries = (
        await session.scalars(
            select(AdministrationAudit).order_by(AdministrationAudit.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(entry.id),
            "action": entry.action,
            "actor": entry.actor,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "detail": entry.detail,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]


def _project(item: Project) -> dict:
    return {
        "id": str(item.id),
        "folder_id": str(item.folder_id) if item.folder_id else None,
        "title": item.title,
        "request": item.request,
        "original_request": item.original_request,
        "target_duration_minutes": item.target_duration_minutes,
        "mode": item.mode,
        "language": item.language,
        "text_language": item.text_language,
        "voice_language": item.voice_language,
        "status": item.status,
        "pinned": item.pinned,
        "archived": item.archived,
        "video_path": item.active_video_path,
        "visibility": item.visibility,
        "subject": item.subject,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _scene(item: Scene) -> dict:
    return {
        "id": str(item.id),
        "scene_id": item.scene_key,
        "title": item.title,
        "order": item.order_index,
        "status": item.status,
        "duration": item.duration,
        "start": item.timeline_start,
        "end": item.timeline_end,
        "versions": {
            "spec": item.active_spec_version,
            "code": item.active_code_version,
            "audio": item.active_audio_version,
            "render": item.active_render_version,
        },
        "verification": item.verification_state,
        "repair_attempts": item.repair_attempts,
    }


# Versioned project API. Legacy unversioned routes remain temporarily for the
# current frontend; all new clients must use these paths.
for _path, _endpoint, _methods, _status in (
    ("/v1/projects", create_project, ["POST"], 201),
    ("/v1/projects", list_projects, ["GET"], None),
    ("/v1/projects/{project_id}", get_project, ["GET"], None),
    ("/v1/projects/{project_id}/settings", update_project_settings, ["PATCH"], None),
    ("/v1/projects/{project_id}/memory", project_memory, ["GET"], None),
    ("/v1/projects/{project_id}/revisions", project_revisions, ["GET"], None),
    ("/v1/projects/{project_id}/cancel", cancel_project, ["POST"], 202),
    ("/v1/projects/{project_id}/sources", upload_source, ["POST"], 201),
    ("/v1/projects/{project_id}/sources/link", import_source_link, ["POST"], 201),
    ("/v1/projects/{project_id}/generate", generate, ["POST"], 202),
    ("/v1/projects/{project_id}/progress", progress, ["GET"], None),
    ("/v1/projects/{project_id}/chat", tutor_messages, ["GET"], None),
    ("/v1/projects/{project_id}/chat", tutor_chat, ["POST"], None),
    ("/v1/projects/{project_id}/chat/{message_id}", edit_tutor_message, ["PATCH"], None),
    ("/v1/projects/{project_id}/scenes", scenes, ["GET"], None),
    ("/v1/projects/{project_id}/video", video, ["GET"], None),
    ("/v1/projects/{project_id}/videos", video_versions, ["GET"], None),
    ("/v1/projects/{project_id}/subtitles.vtt", subtitles_vtt, ["GET"], None),
    ("/v1/projects/{project_id}/timeline", timeline, ["GET"], None),
    ("/v1/projects/{project_id}/chapters.vtt", chapters_vtt, ["GET"], None),
    ("/v1/projects/{project_id}/feedback", feedback, ["POST"], 202),
    ("/v1/projects/{project_id}/edit-range", edit_range, ["POST"], 202),
    ("/v1/projects/{project_id}/scenes/{scene_id}/edit", edit_scene, ["POST"], 202),
    ("/v1/projects/{project_id}/scenes/{scene_id}/regenerate", regenerate_scene, ["POST"], 202),
    ("/v1/projects/{project_id}/scenes/{scene_id}/localize", localize_scene, ["POST"], 202),
    ("/v1/projects/{project_id}/scenes/{scene_id}/versions", versions, ["GET"], None),
    (
        "/v1/projects/{project_id}/scenes/{scene_id}/restore/{version}",
        restore_version,
        ["POST"],
        None,
    ),
):
    app.add_api_route(_path, _endpoint, methods=_methods, status_code=_status)


def run() -> None:
    import uvicorn

    uvicorn.run("scholarmotion.api.main:app", host="0.0.0.0", port=8000, reload=False)
