"""Core Pydantic schemas: requests, scene specs, artifacts, and verification reports."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class RequestType(StrEnum):
    CONCEPT_EXPLANATION = "concept_explanation"
    PAPER_EXPLANATION = "paper_explanation"
    PAPER_SECTION_EXPLANATION = "paper_section_explanation"
    EQUATION_EXPLANATION = "equation_explanation"
    FIGURE_EXPLANATION = "figure_explanation"
    RESULTS_EXPLANATION = "results_explanation"
    COMPARISON = "comparison"


class SceneStatus(StrEnum):
    PLANNED = "PLANNED"
    SPEC_READY = "SPEC_READY"
    CODE_GENERATING = "CODE_GENERATING"
    AUDIO_GENERATING = "AUDIO_GENERATING"
    CODE_READY = "CODE_READY"
    AUDIO_READY = "AUDIO_READY"
    TIMING_RECONCILING = "TIMING_RECONCILING"
    DRAFT_RENDERING = "DRAFT_RENDERING"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    FINAL_RENDERING = "FINAL_RENDERING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class FeedbackType(StrEnum):
    CONTENT_EDIT = "CONTENT_EDIT"
    STYLE_PREFERENCE = "STYLE_PREFERENCE"
    VISUAL_DEFECT = "VISUAL_DEFECT"
    MATH_DEFECT = "MATH_DEFECT"
    SEMANTIC_DEFECT = "SEMANTIC_DEFECT"
    AUDIO_DEFECT = "AUDIO_DEFECT"
    TIMING_DEFECT = "TIMING_DEFECT"
    SCOPE_CHANGE = "SCOPE_CHANGE"


class ArtifactKind(StrEnum):
    PREPARATION = "preparation"
    PROFILE = "profile"
    RETRIEVAL = "retrieval"
    CURRICULUM = "curriculum"
    PEDAGOGY = "pedagogy"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    SPEC = "spec"
    CODE = "code"
    AUDIO = "audio"
    TIMING = "timing"
    SUBTITLES = "subtitles"
    RENDER = "render"
    VERIFICATION = "verification"
    VIDEO = "video"
    TIMELINE = "timeline"


class LearnerProfile(BaseModel):
    topic: str
    target_level: str = "general"
    known_concepts: list[str] = Field(default_factory=list)
    unknown_concepts: list[str] = Field(default_factory=list)
    desired_duration_minutes: float = Field(3, gt=0, le=30)
    language: str = "English"
    math_depth: str = "appropriate"
    request_type: RequestType = RequestType.CONCEPT_EXPLANATION


class SourceRef(BaseModel):
    source_id: str
    document_id: str
    page: int | None = None
    section: str | None = None


class KnowledgeCard(BaseModel):
    card_id: str
    kind: Literal["fact", "equation", "example", "definition", "figure"]
    content: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0, le=1)


class PlotSeries(BaseModel):
    name: str
    values: list[float]


class DataPlot(BaseModel):
    """Numbers copied from a source table, redrawn as a chart in a scene."""

    plot_id: str
    title: str
    kind: Literal["bar", "line"] = "bar"
    x_label: str = ""
    y_label: str = ""
    categories: list[str]
    series: list[PlotSeries]
    source: str = ""

    def is_consistent(self) -> bool:
        return bool(self.categories and self.series) and all(
            len(series.values) == len(self.categories) for series in self.series
        )


class FigureRegion(BaseModel):
    """A measured part of a paper figure to highlight, as fractions from its top-left."""

    name: str
    box: list[float]  # x0, y0, x1, y1
    point: list[float] | None = None  # fx, fy: where an arrow points (curves)


class PaperFigure(BaseModel):
    """A figure cropped from the source paper, shown as an image in a scene."""

    label: str  # "Figure 3"
    caption: str = ""
    filename: str  # under the render's assets/ directory
    path: str = ""  # absolute path of the crop on this machine
    width: int = 0
    height: int = 0
    focus: str = ""  # what the lecture should point at in it
    # Located by a vision model before the scene is written; the code model
    # uses these boxes as given instead of estimating positions itself.
    regions: list[FigureRegion] = Field(default_factory=list)


class CurriculumItem(BaseModel):
    title: str
    objective: str
    duration_seconds: float = Field(gt=0)
    prerequisites: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    # Paper lectures: what this chapter must teach, verbatim from the source.
    key_points: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    plots: list[DataPlot] = Field(default_factory=list)
    figures: list[PaperFigure] = Field(default_factory=list)
    # Curated lessons: what is worth showing on screen in this part.
    visual_ideas: list[str] = Field(default_factory=list)


class SourceReference(BaseModel):
    """Detail the plan left out, kept in the vector store for a later lookup."""

    about: str
    source_ids: list[str] = Field(default_factory=list)


class CurriculumPlan(BaseModel):
    topic: str
    items: list[CurriculumItem]
    skipped_known_concepts: list[str] = Field(default_factory=list)
    # paper = a paper lecture plan; curated = the lesson curator's plan;
    # outline = the plain time split used when no model plan is available.
    kind: str = "outline"
    # What kind of ask this lesson answers, and how the curator suggests closing it.
    ask_kind: str = ""
    ending: str = ""
    references: list[SourceReference] = Field(default_factory=list)


class NarrationBlock(BaseModel):
    block_id: str
    chapter_id: str
    title: str = ""
    text: str
    learning_objective: str
    source_ids: list[str] = Field(default_factory=list)
    estimated_duration_seconds: float = Field(gt=0)
    defined_symbols: dict[str, str] = Field(default_factory=dict)
    equations: list[str] = Field(default_factory=list)
    data_plots: list[DataPlot] = Field(default_factory=list)
    paper_figures: list[PaperFigure] = Field(default_factory=list)
    # English STEM terms this block names; kept in English when localized.
    key_terms: list[str] = Field(default_factory=list)


class VisualBeat(BaseModel):
    beat_id: str
    narration_segment: str
    visual: str
    primitive: str = "diagram"
    start_seconds: float | None = None
    end_seconds: float | None = None


class LayoutSpec(BaseModel):
    main_visual_region: str = "left"
    equation_region: str = "upper_right"
    subtitle_safe_area: str = "bottom"
    min_text_height: float = 0.3


class SceneSpec(BaseModel):
    scene_id: str
    chapter_id: str
    title: str
    learning_objective: str
    duration_target_seconds: float = Field(gt=0, le=600)
    narration: str
    visual_beats: list[VisualBeat]
    equations: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)
    data_plots: list[DataPlot] = Field(default_factory=list)
    paper_figures: list[PaperFigure] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    start_visual_state: list[str] = Field(default_factory=list)
    end_visual_state: list[str] = Field(default_factory=list)
    layout: LayoutSpec = Field(default_factory=LayoutSpec)
    verification: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: int = Field(1, ge=1)


class GeneratedSceneCode(BaseModel):
    scene_class: str
    python_code: str
    assets: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    timing_markers: dict[str, float] = Field(default_factory=dict)


class TimingCue(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> TimingCue:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class TTSResult(BaseModel):
    voice_id: str | None = None
    provider: str | None = None
    model: str | None = None
    audio_path: str
    duration_seconds: float = Field(gt=0)
    sentence_timings: list[TimingCue]
    word_timings: list[TimingCue] = Field(default_factory=list)


class STTResult(BaseModel):
    text: str
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class SceneCodeRequest(BaseModel):
    scene: SceneSpec
    corrections: list[str] = Field(default_factory=list)
    repair_feedback: list[str] = Field(default_factory=list)


class VisualVerificationRequest(BaseModel):
    scene: SceneSpec
    images_base64: list[str] = Field(default_factory=list, max_length=12)


class TTSSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    language: str = "English"


class STTTranscriptionRequest(BaseModel):
    audio_base64: str = Field(min_length=1)
    language: str | None = None


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    source_language: str | None = None
    target_language: str = Field(default="English", min_length=1, max_length=80)


class TranslationResponse(BaseModel):
    text: str
    source_language: str | None = None
    target_language: str


class SkillResourceInput(BaseModel):
    path: str
    media_type: str = "text/markdown"
    content: str = ""


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    capabilities: list[str] = Field(min_length=1)
    instructions: str
    allowed_tools: list[str] = Field(default_factory=list)
    resources: list[SkillResourceInput] = Field(default_factory=list)


class SkillVersionRequest(BaseModel):
    description: str
    instructions: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    resources: list[SkillResourceInput] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    scene_id: str
    category: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    frames: list[int] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    description: str
    suggested_repair: str
    correction_memory_candidate: bool = False


class VerificationReport(BaseModel):
    scene_id: str
    passed: bool
    score: float = Field(ge=0, le=1)
    issues: list[VerificationIssue] = Field(default_factory=list)
    layers: dict[str, bool] = Field(default_factory=dict)


class TimelineSegment(BaseModel):
    scene_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    chapter_id: str | None = None
    render_path: str | None = None
    render_version: int = 1

    @model_validator(mode="after")
    def ordered(self) -> TimelineSegment:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Timeline(BaseModel):
    video_id: str
    scenes: list[TimelineSegment]
    duration_seconds: float


class FeedbackClassification(BaseModel):
    feedback_type: FeedbackType
    category: str | None = None
    instruction: str
    memory_eligible: bool = False


class EditRangeRequest(BaseModel):
    video_id: str | None = None
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    instruction: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> EditRangeRequest:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ParsedEdit(BaseModel):
    operation: Literal["edit_time_range", "edit_scene"] = "edit_time_range"
    start_seconds: float | None = None
    end_seconds: float | None = None
    scene_id: str | None = None
    instruction: str


class ProjectCreate(BaseModel):
    title: str
    request: str
    target_duration_minutes: float | None = Field(default=None, gt=0, le=30)
    mode: Literal["chat", "video", "both"] = "both"
    language: str | None = None
    text_language: str | None = None
    voice_language: str | None = None
    folder_id: UUID | None = None

    @model_validator(mode="after")
    def presentation_languages(self) -> ProjectCreate:
        self.language = self.language.strip() if self.language else None
        self.text_language = self.text_language.strip() if self.text_language else None
        self.voice_language = self.voice_language.strip() if self.voice_language else None
        return self


class SourceLinkRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ProjectRead(ProjectCreate):
    id: UUID
    status: str
    created_at: datetime


class ProjectSettingsUpdate(BaseModel):
    target_duration_minutes: float | None = Field(default=None, gt=0, le=30)
    text_language: str | None = None
    voice_language: str | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pinned: bool | None = None
    archived: bool | None = None


class ProjectFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class ProjectFolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class ProjectMoveRequest(BaseModel):
    folder_id: UUID | None = None


class ProjectCopyRequest(ProjectMoveRequest):
    title: str | None = Field(default=None, min_length=1, max_length=300)


class WorkspaceProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    default_duration_minutes: float | None = Field(default=None, gt=0, le=30)
    default_text_language: str | None = None
    default_voice_language: str | None = None


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class UserLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class UserAccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    default_duration_minutes: float | None = Field(default=None, gt=0, le=30)
    default_text_language: str | None = None
    default_voice_language: str | None = None
    # Research depth values by key; out-of-range values are clamped on save.
    research_settings: dict[str, int] | None = None


class ProgressEvent(BaseModel):
    event: str
    project_id: str
    scene_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TutorChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    language: str | None = None
    text_language: str | None = None
    voice_language: str | None = None
    # Thinking budget for the explanation; None uses the server default.
    reasoning_effort: Literal["low", "medium", "high"] | None = None


class TemporaryChatRequest(TutorChatRequest):
    history: list[str] = Field(default_factory=list, max_length=40)


class TutorMessageEdit(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class TutorChatResponse(BaseModel):
    role: Literal["assistant"] = "assistant"
    message: str
    action: Literal[
        "answer", "edit_range", "generate_video", "localize", "localize_scene", "wait"
    ] = "answer"
    canonical_message: str
    range_start: float | None = None
    range_end: float | None = None
    workflow_id: str | None = None
    run_id: str | None = None
