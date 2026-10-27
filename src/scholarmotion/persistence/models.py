"""SQLAlchemy models for accounts, chats, projects, scenes, and versioned artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scholarmotion.persistence.database import Base


def now() -> datetime:
    return datetime.now(UTC)


class IdMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectFolder(Base, IdMixin):
    """A user-owned workspace that can contain multiple tutor conversations."""

    __tablename__ = "project_folders"
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("owner_id", "name"),)


class Project(Base, IdMixin):
    __tablename__ = "projects"
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    folder_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_folders.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    request: Mapped[str] = mapped_column(Text)
    # `request` is the canonical English request consumed by every planning,
    # scripting, and code agent. Retain the learner's original words for chat.
    original_request: Mapped[str | None] = mapped_column(Text)
    target_duration_minutes: Mapped[float] = mapped_column(Float, default=3)
    # A project is a durable conversation. Rendering is an optional capability
    # of that conversation, rather than a separate UI session.
    mode: Mapped[str] = mapped_column(String(20), default="both", index=True)
    language: Mapped[str] = mapped_column(String(80), default="English")
    text_language: Mapped[str] = mapped_column(String(80), default="English")
    voice_language: Mapped[str] = mapped_column(String(80), default="English")
    status: Mapped[str] = mapped_column(String(40), default="CREATED", index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active_video_path: Mapped[str | None] = mapped_column(Text)
    active_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    scenes: Mapped[list[Scene]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class WorkspaceProfile(Base, IdMixin):
    """Single local-workspace profile until authenticated accounts are added."""

    __tablename__ = "workspace_profiles"
    profile_key: Mapped[str] = mapped_column(String(100), unique=True, default="local")
    display_name: Mapped[str] = mapped_column(String(120), default="Learner")
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_color: Mapped[str] = mapped_column(String(20), default="#10a37f")
    default_duration_minutes: Mapped[float] = mapped_column(Float, default=3)
    default_text_language: Mapped[str] = mapped_column(String(80), default="English")
    default_voice_language: Mapped[str] = mapped_column(String(80), default="English")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class UserAccount(Base, IdMixin):
    __tablename__ = "user_accounts"
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_color: Mapped[str] = mapped_column(String(20), default="#10a37f")
    default_duration_minutes: Mapped[float] = mapped_column(Float, default=3)
    default_text_language: Mapped[str] = mapped_column(String(80), default="English")
    default_voice_language: Mapped[str] = mapped_column(String(80), default="English")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class UserSession(Base, IdMixin):
    __tablename__ = "user_sessions"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class AdministrationAudit(Base, IdMixin):
    """Append-only record of privileged global content changes.

    The API authenticates an administrator by a shared credential, so storing
    the credential or its hash would add risk without identifying a person.
    Keep a stable actor label and the reviewed target/payload instead.
    """

    __tablename__ = "administration_audit"
    action: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="admin_api_key")
    target_type: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[str] = mapped_column(String(320), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProjectRevision(Base, IdMixin):
    """Immutable project-level request/configuration snapshot."""

    __tablename__ = "project_revisions"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="created")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("project_id", "version"),)


class ProjectArtifact(Base, IdMixin):
    __tablename__ = "project_artifacts"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(50), index=True)
    version: Mapped[int] = mapped_column(Integer)
    uri: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("project_id", "kind", "version"),)


class SourceDocument(Base, IdMixin):
    __tablename__ = "source_documents"
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="paper")
    title: Mapped[str] = mapped_column(String(500))
    authors: Mapped[list[str]] = mapped_column(JSON, default=list)
    uri: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class SourceChunk(Base, IdMixin):
    __tablename__ = "source_chunks"
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    class_level: Mapped[int | None] = mapped_column(Integer, index=True)
    subject: Mapped[str | None] = mapped_column(String(100), index=True)
    book: Mapped[str | None] = mapped_column(String(250))
    chapter: Mapped[str | None] = mapped_column(String(250), index=True)
    section: Mapped[str | None] = mapped_column(String(250), index=True)
    page: Mapped[int | None] = mapped_column(Integer, index=True)
    content_type: Mapped[str] = mapped_column(String(40), default="paragraph", index=True)
    text: Mapped[str] = mapped_column(Text)
    equations: Mapped[list[str]] = mapped_column(JSON, default=list)
    examples: Mapped[list[str]] = mapped_column(JSON, default=list)
    definitions: Mapped[list[str]] = mapped_column(JSON, default=list)
    concept_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    prerequisite_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Embeddings are held in Qdrant. This is only the synchronisation marker
    # used to rebuild an index after an interrupted ingest.
    vector_revision: Mapped[int] = mapped_column(Integer, default=0)


class SourceClaim(Base, IdMixin):
    __tablename__ = "source_claims"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    claim: Mapped[str] = mapped_column(Text)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    claim_type: Mapped[str] = mapped_column(String(30))


class ConceptNode(Base, IdMixin):
    __tablename__ = "concept_nodes"
    name: Mapped[str] = mapped_column(String(250), unique=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ConceptEdge(Base, IdMixin):
    __tablename__ = "concept_edges"
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[UUID] = mapped_column(
        ForeignKey("concept_nodes.id", ondelete="CASCADE"), index=True
    )
    edge_type: Mapped[str] = mapped_column(String(40), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1)
    __table_args__ = (UniqueConstraint("source_id", "target_id", "edge_type"),)


class LessonBlueprint(Base, IdMixin):
    __tablename__ = "lesson_blueprints"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Chapter(Base, IdMixin):
    __tablename__ = "chapters"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_key: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    order_index: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("project_id", "chapter_key"),)


class Scene(Base, IdMixin):
    __tablename__ = "scenes"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    scene_key: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    order_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="PLANNED", index=True)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    active_spec_version: Mapped[int] = mapped_column(Integer, default=1)
    active_code_version: Mapped[int] = mapped_column(Integer, default=0)
    active_audio_version: Mapped[int] = mapped_column(Integer, default=0)
    active_render_version: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    timeline_start: Mapped[float | None] = mapped_column(Float)
    timeline_end: Mapped[float | None] = mapped_column(Float)
    verification_state: Mapped[str | None] = mapped_column(String(40))
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship(back_populates="scenes")
    versions: Mapped[list[SceneVersion]] = relationship(
        back_populates="scene", cascade="all, delete-orphan"
    )
    __table_args__ = (UniqueConstraint("project_id", "scene_key"),)


class SceneVersion(Base, IdMixin):
    __tablename__ = "scene_versions"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="initial")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scene: Mapped[Scene] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("scene_id", "version"),)


class NarrationSegment(Base, IdMixin):
    __tablename__ = "narration_segments"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    segment_key: Mapped[str] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(Text)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class StoryboardBeat(Base, IdMixin):
    __tablename__ = "storyboard_beats"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    beat_key: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SceneSpecification(Base, IdMixin):
    __tablename__ = "scene_specifications"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    uri: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("scene_id", "version"),)


class GeneratedCode(Base, IdMixin):
    __tablename__ = "generated_codes"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec_version: Mapped[int] = mapped_column(Integer)
    uri: Mapped[str] = mapped_column(Text)
    scene_class: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class AudioArtifact(Base, IdMixin):
    __tablename__ = "audio_artifacts"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    narration_version: Mapped[int] = mapped_column(Integer)
    uri: Mapped[str] = mapped_column(Text)
    duration: Mapped[float] = mapped_column(Float)
    timings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class RenderArtifact(Base, IdMixin):
    __tablename__ = "render_artifacts"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    code_version: Mapped[int] = mapped_column(Integer)
    audio_version: Mapped[int | None] = mapped_column(Integer)
    uri: Mapped[str] = mapped_column(Text)
    log_uri: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class VerificationRun(Base, IdMixin):
    __tablename__ = "verification_runs"
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    render_version: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    layers: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)


class VerificationIssue(Base, IdMixin):
    __tablename__ = "verification_issues"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class FeedbackEvent(Base, IdMixin):
    __tablename__ = "feedback_events"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scene_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    feedback_type: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str | None] = mapped_column(String(100))
    instruction: Mapped[str] = mapped_column(Text)
    range_start: Mapped[float | None] = mapped_column(Float)
    range_end: Mapped[float | None] = mapped_column(Float)


class CorrectionCandidate(Base, IdMixin):
    __tablename__ = "correction_candidates"
    category: Mapped[str] = mapped_column(String(100), index=True)
    trigger_conditions: Mapped[str] = mapped_column(Text)
    recommended_fix: Mapped[str] = mapped_column(Text)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class CorrectionEntry(Base, IdMixin):
    __tablename__ = "correction_entries"
    correction_key: Mapped[str] = mapped_column(String(50), unique=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    trigger_conditions: Mapped[str] = mapped_column(Text)
    anti_pattern: Mapped[str] = mapped_column(Text)
    required_behavior: Mapped[str] = mapped_column(Text)
    recommended_fix: Mapped[str] = mapped_column(Text)
    evidence_count: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    applicable_model: Mapped[str] = mapped_column(String(200), default="any")
    status: Mapped[str] = mapped_column(String(30), default="active")
    validation_tests: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Skill(Base, IdMixin):
    """Stable identity for an Agent Skills-compatible skill package."""

    __tablename__ = "skills"
    skill_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    active_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    versions: Mapped[list[SkillVersion]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class SkillVersion(Base, IdMixin):
    """The database representation of a skill's SKILL.md front matter and body."""

    __tablename__ = "skill_versions"
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(1024))
    instructions: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    skill: Mapped[Skill] = relationship(back_populates="versions")
    resources: Mapped[list[SkillResource]] = relationship(
        back_populates="skill_version", cascade="all, delete-orphan"
    )
    __table_args__ = (UniqueConstraint("skill_id", "version"),)


class SkillResource(Base, IdMixin):
    """Optional `references/`, `assets/`, or `scripts/` member of a skill package."""

    __tablename__ = "skill_resources"
    skill_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"), index=True
    )
    resource_path: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(120), default="text/markdown")
    content: Mapped[str | None] = mapped_column(Text)
    object_uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    skill_version: Mapped[SkillVersion] = relationship(back_populates="resources")
    __table_args__ = (UniqueConstraint("skill_version_id", "resource_path"),)


class AgentTool(Base, IdMixin):
    """An approved callable surface that a skill may request by name."""

    __tablename__ = "agent_tools"
    tool_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(1024))
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class StyleProfile(Base, IdMixin):
    __tablename__ = "style_profiles"
    profile_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PronunciationEntry(Base, IdMixin):
    __tablename__ = "pronunciation_entries"
    term: Mapped[str] = mapped_column(String(300), index=True)
    language: Mapped[str] = mapped_column(String(30), default="en")
    phonetic_hint: Mapped[str] = mapped_column(String(500))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("term", "language", "project_id"),)


class WorkflowExecution(Base, IdMixin):
    __tablename__ = "workflow_executions"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(255), unique=True)
    run_id: Mapped[str | None] = mapped_column(String(255))
    workflow_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TutorMessage(Base, IdMixin):
    """A persisted tutor conversation, separate from mutable lesson artifacts."""

    __tablename__ = "tutor_messages"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    canonical_text: Mapped[str] = mapped_column(Text)
    display_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(80), default="English")
    action: Mapped[str | None] = mapped_column(String(50))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class TimelineSegment(Base, IdMixin):
    __tablename__ = "timeline_segments"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    video_version: Mapped[int] = mapped_column(Integer)
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    render_version: Mapped[int] = mapped_column(Integer)


class ModelInvocation(Base, IdMixin):
    __tablename__ = "model_invocations"
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(50))
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_artifact_id: Mapped[str | None] = mapped_column(String(100))
    temperature: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)


class ProgressEvent(Base, IdMixin):
    __tablename__ = "progress_events"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(100), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
