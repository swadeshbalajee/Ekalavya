"""Lesson pipeline: plan, script, produce scenes in parallel, review, repair, assemble."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import BaseModel, Field

from scholarmotion.agents.code_generator import (
    CodeGenerationError,
    generate_scene_code,
    scene_class_name,
)
from scholarmotion.agents.curriculum_agent import build_curriculum
from scholarmotion.agents.knowledge_gatherer import gather_knowledge
from scholarmotion.agents.learner_profiler import profile_request
from scholarmotion.agents.lesson_curator import LessonCurator, plan_evidence
from scholarmotion.agents.lesson_preparation import LessonPreparation
from scholarmotion.agents.manager import Manager, RepairAction
from scholarmotion.agents.paper_outline import (
    PaperOutline,
    curriculum_from_outline,
    outline_evidence,
)
from scholarmotion.agents.pedagogical_agent import build_teaching_dossier
from scholarmotion.agents.reference_lookup import look_up
from scholarmotion.agents.scene_compiler import compile_scene_specs
from scholarmotion.agents.script_writer import SOURCE_POLICIES, revise_narration, write_script
from scholarmotion.agents.storyboard_agent import plan_storyboard
from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.manim_runtime.sandbox import RenderResult, SandboxRenderer
from scholarmotion.manim_runtime.timing import reconcile_timing, scale_manim_durations
from scholarmotion.media.alignment import scene_cuts, sentence_timings_from_words, words_between
from scholarmotion.media.audio import align_visual_beats, estimate_sentence_timings, scale_timings
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.media.narration import split_lesson_wav
from scholarmotion.media.pauses import (
    insert_pauses,
    map_pauses,
    sentences,
    speakable_with_pauses,
    with_markers,
)
from scholarmotion.media.pronunciation import respell, restore_spelling
from scholarmotion.media.speech import to_speakable
from scholarmotion.media.subtitles import cues_to_srt, cues_to_vtt, merge_scene_cues
from scholarmotion.media.timeline import build_timeline, media_duration
from scholarmotion.memory.correction_memory import CorrectionMemory
from scholarmotion.observability.usage import set_usage_scene, usage_stage
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers.llm.base import call_timeout
from scholarmotion.retrieval.hybrid_retrieval import HybridRetriever
from scholarmotion.schemas import (
    CurriculumPlan,
    NarrationBlock,
    SceneSpec,
    Timeline,
    TimingCue,
    TTSResult,
    VerificationIssue,
    VerificationReport,
    VisualBeat,
)
from scholarmotion.services.research_settings import ResearchSettings
from scholarmotion.skills import SkillService
from scholarmotion.translation import TranslationService


def _scheduled_fallback_matches_audio(python_code: str, sentence_timings: list) -> bool:
    """Check a known deterministic template's total timeline against measured speech."""
    if "SCHOLARMOTION_DETERMINISTIC_FALLBACK" not in python_code:
        return False
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return False

    actions: list[tuple[int, int, str, float]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "self":
            continue
        if node.func.attr not in {"wait", "play"}:
            continue
        if node.func.attr == "wait":
            if not node.args:
                return False
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, (int, float)):
                actions.append((node.lineno, node.col_offset, "wait", float(argument.value)))
            else:
                return False
        else:
            runtime = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "run_time"), None
            )
            argument = runtime or ast.Constant(value=1.0)
            if isinstance(argument, ast.Constant) and isinstance(argument.value, (int, float)):
                actions.append((node.lineno, node.col_offset, "play", float(argument.value)))
            else:
                return False
    if not sentence_timings or not actions:
        return False
    ordered = sorted(actions)
    wait_count = sum(action == "wait" for _, _, action, _ in ordered)
    if wait_count == len(sentence_timings):
        measured_cues: list[float] = []
        cue_duration = 0.0
        for _, _, action, duration in ordered:
            cue_duration += duration
            if action == "wait":
                measured_cues.append(cue_duration)
                cue_duration = 0.0
        if cue_duration > 0.15:
            return False
    elif wait_count == 0 and sum(action == "play" for _, _, action, _ in ordered) == len(
        sentence_timings
    ):
        measured_cues = [duration for _, _, _, duration in ordered]
    else:
        return False
    expected_cues = [cue.end - cue.start for cue in sentence_timings]
    return all(
        abs(measured - expected) <= max(0.2, expected * 0.02)
        for measured, expected in zip(measured_cues, expected_cues, strict=True)
    )


def _fallback_video_findings(issues: list[dict]) -> list[dict]:
    """Keep visual findings when exact fallback cue timing is independently checked."""
    timing_categories = {
        "audio_video_sync",
        "narration_after_visual",
        "stale_visuals",
    }
    return [
        issue
        for issue in issues
        if not any(
            str(issue.get("category", "")).endswith(category) for category in timing_categories
        )
    ]


from scholarmotion.retrieval.figure_regions import locate_figure_regions
from scholarmotion.verification.aggregator import aggregate_reports
from scholarmotion.verification.checklist import checklist_prompt, edit_check, score_checklist
from scholarmotion.verification.figures import verify_paper_figures_shown
from scholarmotion.verification.layout import is_text, verify_layout
from scholarmotion.verification.math import verify_required_equations
from scholarmotion.verification.pedagogy import verify_pedagogy, verify_rendered_storyboard
from scholarmotion.verification.render import (
    verify_cue_overflow,
    verify_no_raw_latex_text,
    verify_render,
)
from scholarmotion.verification.semantic import verify_source_grounding
from scholarmotion.verification.visual import verify_visual

logger = logging.getLogger(__name__)

# One TTS request is kept below provider input limits; longer lessons are
# spoken as several takes split at chapter boundaries.
LESSON_NARRATION_MAX_CHARS = 4500


# Scenes up to this length are reviewed in a single pass, start to finish.
WHOLE_SCENE_REVIEW_SECONDS = 90.0


def scene_review_prompt(
    *,
    objective: str,
    tags: list[str],
    narration: str,
    cues: list[tuple],
    source_code: str,
    notes: str = "",
    extra: tuple = (),
    measured: str = "",
) -> str:
    """Ask a video model to check a scene item by item, as a student would watch it."""
    cue_lines = "\n".join(f"{start:.2f}-{end:.2f}s: {text}" for start, end, text in cues)
    return (
        "You review one scene of a narrated STEM lesson for a student. You receive the rendered "
        "video, its narration audio (same start time) and stills from it.\n"
        "Judge like a careful teacher, not a pixel checker. Judge only settled frames, where "
        "nothing is moving: objects fading in or out, moving or transforming are never a fault, "
        "even if they cross something on the way; give the settled time where you saw a problem. "
        "Small text that is still clearly readable is fine. "
        "A correct diagram held still while it is explained is fine. Fail an item only for a "
        "problem a student would notice. Look closely at the stills: labels sitting on bars or "
        "lines, a highlight on the wrong part of a figure, and boxes with nothing in them are "
        "easy to miss in motion. If lesson_closing is tagged, the scene must end on a settled "
        "takeaway, not a teaser or an abrupt cut.\n"
        + checklist_prompt(extra)
        + (f"\n{measured}\n" if measured else "")
        + "\n(The source is given only to suggest code changes; judge what you see and hear.)\n"
        + (f"{notes}\n" if notes else "")
        + f"Scene objective: {objective}\nScene tags: {tags}\nNarration: {narration}\n"
        f"Estimated sentence timing (not forced alignment):\n{cue_lines}\n"
        f"Scene source code:\n{source_code}"
    )


def _grouped_repair_feedback(issues: list[VerificationIssue], examples: int = 3) -> list[str]:
    """Summarize findings per category for the code model.

    A 4x4 matrix can yield dozens of identical "text too small" findings; sending
    each one buried the real problems and slowed repair convergence.
    """
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    groups: dict[str, list[VerificationIssue]] = {}
    for issue in sorted(issues, key=lambda item: order.get(item.severity, 4)):
        groups.setdefault(issue.category, []).append(issue)
    feedback = []
    for category, items in groups.items():
        lines = [
            f"{issue.description}\nSuggested repair: {issue.suggested_repair}"
            for issue in items[:examples]
        ]
        if len(items) > examples:
            others = sorted({name for issue in items[examples:] for name in issue.objects})
            lines.append(
                f"...and {len(items) - examples} more {category} findings of the same kind"
                + (f" (objects: {', '.join(others[:20])})" if others else "")
                + ". Fix the shared root cause (for example font size, spacing or layout "
                "region), not each object separately."
            )
        feedback.append(f"{category} ({len(items)} findings):\n" + "\n".join(lines))
    return feedback


def _issue_reasons(report: VerificationReport, limit: int = 3) -> list[str]:
    """Short, learner-readable rejection reasons ordered by severity."""
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ranked = sorted(report.issues, key=lambda issue: order.get(issue.severity, 4))
    reasons = []
    for issue in ranked[:limit]:
        description = re.sub(
            r"^At keyframe (\d+) \(.*?not necessarily the final frame\):\s*",
            r"[step \1] ",
            issue.description,
        )
        reasons.append(f"{issue.category} ({issue.severity}): {description[:220]}")
    return reasons


def _verify_render_layout(scene_id: str, result: RenderResult) -> list[VerificationIssue]:
    if not result.success or not result.bounds_path:
        return []
    try:
        snapshots = json.loads(Path(result.bounds_path).read_text())
    except (OSError, TypeError, json.JSONDecodeError):
        return []
    seen: set[tuple] = set()
    issues: list[VerificationIssue] = []
    for keyframe_index, snapshot in enumerate(snapshots):
        objects = [ObjectBounds(**item) for item in snapshot]
        for issue in verify_layout(scene_id, objects):
            key = (issue.category, tuple(issue.objects))
            if key in seen:
                continue
            seen.add(key)
            issue.frames = [keyframe_index]
            prefix = (
                f"At keyframe {keyframe_index} (the state right after the "
                f"{keyframe_index + 1}th self.play()/self.wait() call in construct(), not "
                "necessarily the final frame): "
            )
            issue.description = prefix + issue.description
            issue.suggested_repair = prefix + issue.suggested_repair
            issues.append(issue)
    return issues


# The prompt asks for this much space between unrelated labels.
LABEL_GAP = 0.25


def _gap(first: ObjectBounds, second: ObjectBounds) -> float:
    """Space between two boxes; zero or less when they touch or overlap."""
    return max(
        first.left - second.right,
        second.left - first.right,
        first.bottom - second.top,
        second.bottom - first.top,
    )


def measured_overlaps(scene_id: str, bounds_path: str | None, limit: int = 6) -> str:
    """Where rendering measured text overlapping something, or labels crowding each
    other, as places for the reviewer to look.

    Geometry cannot tell a label put on its own bar on purpose from a label
    covering a drawing, so it does not decide; but a reviewer watching the
    whole video misses small collisions, so it is told exactly where they are.
    """
    if not bounds_path:
        return ""
    try:
        snapshots = json.loads(Path(bounds_path).read_text())
    except (OSError, TypeError, json.JSONDecodeError):
        return ""
    found: dict[str, dict] = {}
    crowded: dict[tuple[str, str], set[int]] = {}
    for step, snapshot in enumerate(snapshots, 1):
        objects = {item["name"]: ObjectBounds(**item) for item in snapshot}
        overlapping = set()
        for issue in verify_layout(scene_id, list(objects.values())):
            if issue.category != "layout.overlap" or len(issue.objects) != 2:
                continue
            first, second = (objects.get(name) for name in issue.objects)
            overlapping.add(frozenset(issue.objects))
            for text, other in ((first, second), (second, first)):
                if text is None or other is None or not text.label:
                    continue
                entry = found.setdefault(text.label, {"steps": set(), "with": {}})
                entry["steps"].add(step)
                kind = (
                    f"text '{other.label}'" if other.label else other.name.rsplit("_", 1)[0].lower()
                )
                entry["with"].setdefault(kind, set()).add(other.name)
        # Labels never need to touch: any two closer than LABEL_GAP are worth a look.
        labels = [obj for obj in objects.values() if obj.label and is_text(obj)]
        for index, first in enumerate(labels):
            for second in labels[index + 1 :]:
                pair = frozenset((first.name, second.name))
                if pair in overlapping or _gap(first, second) >= LABEL_GAP - 0.01:
                    continue
                key = tuple(sorted((first.label, second.label)))
                crowded.setdefault(key, set()).add(step)
    if not found and not crowded:
        return ""
    total = len(snapshots)

    def span(steps: set[int]) -> str:
        ordered = sorted(steps)
        where = f"step {ordered[0]}" if len(ordered) == 1 else f"steps {ordered[0]}-{ordered[-1]}"
        return where + f" of {total}" + (" (the final frame)" if ordered[-1] == total else "")

    lines = []
    for label, entry in list(found.items())[:limit]:
        others = ", ".join(
            f"{len(names)} {kind}s" if len(names) > 1 else kind
            for kind, names in entry["with"].items()
        )
        lines.append(f"- text '{label}' overlaps {others} at {span(entry['steps'])}")
    for (first, second), steps in list(crowded.items())[:limit]:
        lines.append(
            f"- text '{first}' and text '{second}' are closer than {LABEL_GAP} at {span(steps)}"
        )
    return (
        "MEASURED OVERLAPS (exact positions recorded while rendering; step k is the settled "
        "picture after the k-th animation; label spaces are removed):\n"
        + "\n".join(lines)
        + "\nLook at each of these spots in the video and stills. Fail text_overlap if the text "
        "is hard to read there, covers part of the drawing, or labels crowd together so it is "
        "unclear which label belongs to what; a label placed on its own bar or shape on purpose "
        "and still easy to read is fine."
    )


class LessonIssue(BaseModel):
    scene_id: str = ""  # the scene with the problem; empty for the lesson as a whole
    problem: str
    # True only when this scene's narration is wrong for the lesson and
    # rewriting that narration fixes it.
    blocking: bool = False
    fix: str = ""  # for a blocking issue: how to rewrite the scene's narration


class LessonFlowReview(BaseModel):
    coherent: bool
    natural_ending: bool
    issues: list[LessonIssue] = Field(default_factory=list)


# At most this many scenes are rebuilt by the whole-lesson review, once.
MAX_REVIEW_REPAIRS = 3


def _scenes_to_repair(review: dict, scenes: list) -> dict[str, str]:
    """scene_id -> narration fix, for blocking issues the review tied to a scene."""
    known = {scene.spec.scene_id for scene in scenes}
    fixes: dict[str, list[str]] = {}
    for issue in review.get("issues") or []:
        if issue.get("blocking") and issue.get("scene_id") in known:
            fixes.setdefault(issue["scene_id"], []).append(
                issue.get("fix") or issue.get("problem") or ""
            )
    return {
        scene: " ".join(text).strip() for scene, text in list(fixes.items())[:MAX_REVIEW_REPAIRS]
    }


@dataclass
class ProducedScene:
    spec: SceneSpec
    code_path: str
    audio: TTSResult
    render_path: str
    report: VerificationReport
    render_version: int = 1
    code_version: int = 1
    audio_version: int = 1
    video_duration_seconds: float | None = None


@dataclass
class BuildResult:
    project_id: str
    scenes: list[ProducedScene]
    video_path: str
    timeline: Timeline
    srt_path: str
    vtt_path: str
    events: list[str]
    blocked_scenes: list[dict] = field(default_factory=list)
    lesson_review: dict = field(default_factory=dict)


def _reject_empty_lesson(scenes: list, blocked_scenes: list[dict]) -> None:
    """Publish whatever scenes were produced; fail only when there is nothing at all.

    A scene that still failed after every fallback is left out and reported to
    the learner, rather than discarding the rest of a finished lesson.
    """
    if scenes:
        return
    blocked_ids = ", ".join(item["scene_id"] for item in blocked_scenes) or "all scenes"
    raise RuntimeError(f"no scene could be produced ({blocked_ids}); nothing to publish.")


def _read_log(path: str | None) -> str:
    """The renderer's captured stdout/stderr, or empty when it is unavailable."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""


def _attempt_rank(
    report: VerificationReport, checklist_score: float | None = None, off_frame: int = 0
) -> tuple[int, int, int, float, float]:
    """Sort key for attempts: fewest critical, then high issues, then fewest objects
    measured outside the frame, then the reviewer's checklist score, then the overall
    score. Lower is better; ties keep the earlier.

    The measurement settles what a reviewer can only estimate: of two attempts
    failing one must-pass item each, the one whose labels are inside the frame wins.
    """
    severities = [issue.severity for issue in report.issues]
    return (
        severities.count("critical"),
        severities.count("high"),
        off_frame,
        -(checklist_score or 0.0),
        -report.score,
    )


def review_cues(timings, pauses, narration: str) -> list[tuple[float, float, str]]:
    """Sentence timings for the reviewer, with each planned pause shown as its own silence.

    A pause is spliced into the audio after its sentence, which stretches that
    sentence's cue; listed that way the reviewer hears "Blue is short." run on
    to the next sentence and reports the pause the learner asked for as missing.
    """
    silences = {
        pause.after_sentence: pause.seconds
        for pause in map_pauses(pauses, len(sentences(narration)), len(timings))
    }
    cues: list[tuple[float, float, str]] = []
    for index, cue in enumerate(timings):
        seconds = silences.get(index, 0.0)
        if seconds and cue.end - cue.start > seconds:
            cues.append((cue.start, cue.end - seconds, cue.text))
            cues.append(
                (
                    cue.end - seconds,
                    cue.end,
                    f"(planned {seconds:g}s silence: the teacher's deliberate pause, no speech)",
                )
            )
        else:
            cues.append((cue.start, cue.end, cue.text))
    return cues


# Layout faults are placement problems: something overlaps, sits too low, or is
# small. They make a scene look worse, not wrong. Everything else — a render
# crash, bad mathematics, an ungrounded claim, a pedagogy violation — changes
# what the scene actually teaches and must never be shipped by falling back.
# Findings about how a rendered scene looks or flows. When repairs run out,
# a scene with only these (none critical) ships marked "degraded" with its
# notes shown, instead of failing a whole lecture over one imperfect scene.
# A scene missing its paper figure is still better than no scene once repairs run out.
COSMETIC_ISSUE_PREFIXES = ("layout.", "video.", "vision.", "figure.", "correction.")


# Findings about the checking itself, not about how the scene was written.
_NOT_LEARNABLE = {
    "video.verification_unavailable",
    "video.checklist.score",
    "video.audio_video_duration_mismatch",
}


def _drop_stale_prompt_title_findings(
    layers: dict[str, list[VerificationIssue]], code: str
) -> dict[str, list[VerificationIssue]]:
    """Remove an impossible prompt-title finding after source has been corrected."""
    has_prompt_title = bool(
        re.search(
            r"(?:SafeTitle|Text)\(\s*['\"](?:create|make|generate|build)\b",
            code,
            flags=re.IGNORECASE,
        )
    )
    if has_prompt_title:
        return layers
    stale_title_pattern = re.compile(
        r"(?:prompt|instruction|task|system prompt|create a .*?(?:lesson|video|explanation))",
        flags=re.IGNORECASE,
    )
    for name in ("vision", "video"):
        layers[name] = [
            issue
            for issue in layers.get(name, [])
            if not (
                "title" in issue.description.casefold()
                and stale_title_pattern.search(issue.description)
            )
        ]
    return layers


def _only_cosmetic(report: VerificationReport | None) -> bool:
    """True when every outstanding issue is presentation-only.

    Used to decide whether an imperfect but rendered scene is better than no
    scene at all. Deliberately conservative: an empty/missing report is not
    treated as cosmetic, and a single critical issue disqualifies the scene.
    """
    if report is None or not report.issues:
        return False
    return all(
        issue.category.startswith(COSMETIC_ISSUE_PREFIXES) and issue.severity != "critical"
        for issue in report.issues
    )


def _render_assets(spec: SceneSpec) -> dict:
    """Paper figure crops the scene loads from ``assets/`` while rendering."""
    paths = [Path(figure.path) for figure in spec.paper_figures if figure.path]
    return {"assets_dir": str(paths[0].parent)} if paths else {}


def plan_narration_takes(
    chapter_ids: list[str], texts: list[str], max_chars: int
) -> list[list[int]]:
    """Group consecutive scenes into narration takes of at most ``max_chars``.

    A take prefers to end at a chapter boundary once it is half full, so pauses
    between takes fall where the lecture changes topic anyway.
    """
    takes: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, (chapter, text) in enumerate(zip(chapter_ids, texts, strict=True)):
        added = len(text) + (2 if current else 0)
        new_chapter = bool(current) and chapter != chapter_ids[current[-1]]
        if current and (size + added > max_chars or (new_chapter and size >= max_chars / 2)):
            takes.append(current)
            current, size, added = [], 0, len(text)
        current.append(index)
        size += added
    if current:
        takes.append(current)
    return takes


class ScholarMotionPipeline:
    """Provider-neutral scene pipeline invoked by Temporal activities."""

    def __init__(
        self,
        *,
        llm,
        code_llm=None,
        code_escalation_llm=None,
        escalate_after_failures: int = 2,
        figure_locators: tuple = (None, None),
        correction_folder=None,
        planner_llm=None,
        vision=None,
        video_verifier=None,
        aligner: Callable[[str, str], list[TimingCue] | None] | None = None,
        tts,
        embeddings,
        storage: LocalObjectStore,
        renderer: SandboxRenderer | None = None,
        assembler: FFmpegAssembler | None = None,
        correction_memory: CorrectionMemory,
        skill_service: SkillService,
        translator: TranslationService | None = None,
        text_language: str = "English",
        voice_language: str = "English",
        max_concurrent_scenes: int = 4,
        progress_callback: Callable[[str, str | None], Awaitable[None]] | None = None,
        static_layout_gate: bool = False,
        long_call_timeout_seconds: float = 420,
    ):
        self.llm = llm
        self.code_llm = code_llm or llm
        # A stronger code model that takes over a scene the code model keeps failing.
        self.code_escalation_llm = code_escalation_llm
        self.escalate_after_failures = max(1, escalate_after_failures)
        # Vision models that box the parts of paper figures a scene points at.
        self.figure_locators = figure_locators
        # Lessons from earlier failures (memory/correction_folder.py); when set it
        # replaces the database correction memory.
        self.correction_folder = correction_folder
        # Light planning steps (storyboard, flow review) may use a cheaper model.
        self.planner_llm = planner_llm or llm
        self.vision = vision
        self.video_verifier = video_verifier
        self.aligner = aligner
        self.tts = tts
        self.embeddings = embeddings
        self.storage = storage
        self.renderer = renderer or SandboxRenderer()
        self.assembler = assembler or FFmpegAssembler()
        self.corrections = correction_memory
        self.skills = skill_service
        self.translator = translator
        self.text_language = text_language
        self.voice_language = voice_language
        self.manager = Manager()
        self.max_concurrent_scenes = max(1, max_concurrent_scenes)
        self.progress_callback = progress_callback
        # Geometry rules gate scenes only when explicitly enabled or when no
        # video reviewer is configured; otherwise they are repair hints.
        self.static_layout_gate = static_layout_gate
        self.long_call_timeout_seconds = long_call_timeout_seconds
        # Per-scene tracks cut from one continuous lesson narration.
        self._lesson_audio: dict[str, TTSResult] = {}
        self._presentation_cache: dict[tuple, tuple[SceneSpec, str]] = {}
        # The learner's research depth; the API sets it per build.
        self.research = ResearchSettings()

    def _planner(self):
        # Test pipelines built with object.__new__ have no planner attribute.
        return getattr(self, "planner_llm", None) or self.llm

    def _depth(self) -> ResearchSettings:
        return getattr(self, "research", None) or ResearchSettings()

    async def _curated_curriculum(
        self,
        project_id: str,
        video_version: int,
        request: str,
        profile,
        collected: list[dict],
        resume: bool,
    ) -> CurriculumPlan | None:
        """The curator's plan for this lesson; a resumed build reuses the saved one."""
        if resume:
            saved = self._read_json(f"{project_id}/artifacts/curriculum/v{video_version}.json")
            if saved and saved.get("kind") == "curated":
                return CurriculumPlan.model_validate(saved)
        await self._notify_progress("curation.started", detail={"passages": len(collected)})
        return await LessonCurator(self.llm, helper=self._planner()).curate(
            request=request,
            chunks=collected,
            minutes=profile.desired_duration_minutes,
            level=profile.target_level,
            source_scope=getattr(self, "_source_scope", "none"),
            one_pass_chars=self._depth().one_pass_chars,
            teaching_notes=getattr(self, "_teaching_notes", ""),
        )

    def _reference_lookup(self, curriculum: CurriculumPlan, collected: list[dict]):
        """``lookup(task, plan)`` for the writers, over everything collected for this lesson."""
        limit = self._depth().reference_lookups
        if curriculum.kind != "curated" or limit <= 0:
            return None
        chunks_by_id = {str(chunk.get("id")): chunk for chunk in collected}
        embeddings = getattr(self, "embeddings", None)

        async def search(query: str) -> list[dict]:
            if embeddings is None or not collected:
                return []
            found = await HybridRetriever(embeddings).search(query, collected, limit=3)
            return [item.chunk for item in found]

        async def lookup(task: str, plan: str) -> str:
            return await look_up(
                self._planner(),
                task=task,
                plan=plan,
                references=curriculum.references,
                chunks_by_id=chunks_by_id,
                search=search,
                max_lookups=limit,
                stage="reference_lookup",
            )

        return lookup

    async def _notify_progress(
        self, event: str, scene_id: str | None = None, detail: dict | None = None
    ) -> None:
        """Report a stage, with an optional learner-readable reason/summary."""
        if self.progress_callback is None:
            return
        try:
            accepts_detail = len(inspect.signature(self.progress_callback).parameters) >= 3
        except (TypeError, ValueError):
            accepts_detail = False
        if accepts_detail:
            await self.progress_callback(event, scene_id, detail or {})
        else:
            await self.progress_callback(event, scene_id)

    def _json(self, key: str, value) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        # A failed project build can be retried with the same logical v1
        # artifacts. Reuse those keys on that retry instead of making the
        # project permanently unrecoverable after a partial run.
        return self.storage.put_bytes(
            key, json.dumps(value, indent=2, default=str).encode(), overwrite=True
        )

    def _read_json(self, key: str):
        path = self.storage.local_path(key)
        if path is None or not Path(path).exists():
            return None
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def resumable(self, project_id: str, request: str) -> bool:
        """True when the last build of this same request stopped before finishing.

        Its saved plan (outline, script, storyboard), narration and every scene
        that already passed are reused, so a retry redoes only what failed.
        """
        state = self._read_json(f"{project_id}/artifacts/build_state.json")
        return bool(state) and not state.get("complete") and state.get("request") == request

    def _resume_scene(self, project_id: str, spec: SceneSpec) -> ProducedScene | None:
        """A scene finished by an earlier attempt of this build, if still valid."""
        base = f"{project_id}/scenes/{spec.scene_id}"
        saved_spec = self._read_json(f"{base}/spec/v{spec.version}.json")
        if not saved_spec or saved_spec.get("narration") != spec.narration:
            return None
        saved = self._read_json(f"{base}/result.json")
        if saved and Path(saved.get("render_path", "")).exists():
            if saved.get("spec_version", spec.version) != spec.version:
                # The whole-lesson review rebuilt this scene with revised narration.
                repaired = self._read_json(f"{base}/spec/v{saved['spec_version']}.json")
                if not repaired:
                    return None
                spec = SceneSpec.model_validate(repaired)
            return ProducedScene(
                spec,
                saved["code_path"],
                TTSResult.model_validate(saved["audio"]),
                saved["render_path"],
                VerificationReport.model_validate(saved["report"]),
                saved["render_version"],
                saved["code_version"],
                saved["audio_version"],
                saved.get("video_duration_seconds"),
            )
        # Builds that predate result.json: the newest passing verification.
        audio = self._read_json(f"{base}/audio/v{spec.version}.json")
        root = self.storage.local_path(f"{base}/verification")
        if not audio or root is None or not Path(root).exists():
            return None
        versions = sorted(
            (int(path.stem[1:]) for path in Path(root).glob("v*.json") if path.stem[1:].isdigit()),
            reverse=True,
        )
        for version in versions:
            report = self._read_json(f"{base}/verification/v{version}.json")
            if not report or not report.get("passed"):
                continue
            render_root = self.storage.local_path(f"{base}/render/v{version}")
            videos = sorted(Path(render_root).rglob(f"{scene_class_name(spec.scene_id)}.mp4"))
            codes = sorted(
                Path(self.storage.local_path(f"{base}/code")).glob("v*.py"),
                key=lambda path: int(path.stem[1:]) if path.stem[1:].isdigit() else 0,
            )
            if not videos or not codes:
                return None
            return ProducedScene(
                spec,
                str(codes[-1]),
                TTSResult.model_validate(audio),
                str(videos[-1]),
                VerificationReport.model_validate(report),
                version,
                int(codes[-1].stem[1:]),
                spec.version,
                self.assembler.probe_duration(str(videos[-1])),
            )
        return None

    def _save_scene_result(self, project_id: str, scene: ProducedScene) -> None:
        self._json(
            f"{project_id}/scenes/{scene.spec.scene_id}/result.json",
            {
                "code_path": scene.code_path,
                "audio": scene.audio.model_dump(mode="json"),
                "render_path": scene.render_path,
                "report": scene.report.model_dump(mode="json"),
                "render_version": scene.render_version,
                "code_version": scene.code_version,
                "audio_version": scene.audio_version,
                "video_duration_seconds": scene.video_duration_seconds,
                "spec_version": scene.spec.version,
            },
        )

    def _fit_narration_to_scene_budget(
        self, audio: TTSResult, *, target_seconds: float, artifact_version: int
    ) -> TTSResult:
        """Keep final lesson length faithful to the requested duration.

        Providers speak the same script at slightly different rates.  A small
        pitch-preserving correction per scene prevents those differences from
        accumulating into minutes across a long paper lesson.  Large changes
        are deliberately left alone: speeding a voice that far harms clarity
        and the normal timing/quality gate will request a better script.
        """
        if target_seconds <= 0 or audio.duration_seconds <= 0:
            return audio
        ratio = audio.duration_seconds / target_seconds
        if not 0.9 <= ratio <= 1.1:
            return audio
        if abs(1 - ratio) <= 0.015:
            return audio
        source = Path(audio.audio_path)
        timed_path = source.with_name(f"{source.stem}.timed-v{artifact_version}{source.suffix}")
        if not self.assembler.retime_wav(source, target_seconds, timed_path):
            return audio
        measured = self.assembler.probe_duration(str(timed_path))
        if measured is None or abs(measured - target_seconds) > max(0.2, target_seconds * 0.02):
            timed_path.unlink(missing_ok=True)
            return audio
        scale = measured / audio.duration_seconds
        return audio.model_copy(
            update={
                "audio_path": str(timed_path),
                "duration_seconds": measured,
                "sentence_timings": scale_timings(audio.sentence_timings, scale),
                "word_timings": scale_timings(audio.word_timings, scale),
            }
        )

    async def build(
        self,
        project_id: str,
        request: str,
        *,
        duration_minutes: float = 5,
        language: str = "English",
        retrieved_chunks: list[dict] | None = None,
        paper_outline: PaperOutline | None = None,
        preparation: LessonPreparation | None = None,
        video_version: int = 1,
        scene_version_base: int = 0,
    ) -> BuildResult:
        """Build one lesson video.

        A chat's later lessons are built as the next ``video_version``; their
        scene artifacts are numbered above ``scene_version_base`` (the newest
        existing scene version) so earlier videos keep their own files.
        """
        events = ["project.started"]
        self._source_scope = preparation.source_scope if preparation else "none"
        self._teaching_notes = preparation.teaching_notes if preparation else ""
        self._video_version = video_version
        profile = profile_request(
            request,
            target_duration_minutes=duration_minutes,
            language=language,
            preparation=preparation,
            has_paper=any(
                chunk.get("document_kind") == "paper" for chunk in (retrieved_chunks or [])
            ),
        )
        if paper_outline is not None:
            # A paper lecture's length and audience come from the paper plan.
            profile = profile.model_copy(
                update={
                    "topic": paper_outline.title,
                    "desired_duration_minutes": duration_minutes,
                    "target_level": (
                        "a university student new to this paper"
                        if profile.target_level == "general"
                        else profile.target_level
                    ),
                }
            )
        resume = self.resumable(project_id, request)
        self.resume = resume
        self._json(
            f"{project_id}/artifacts/build_state.json", {"request": request, "complete": False}
        )
        self._json(f"{project_id}/artifacts/profile/v{video_version}.json", profile)
        cards = gather_knowledge(
            retrieved_chunks
            or [
                {
                    "id": "demo-grounding",
                    "text": f"A grounded introductory explanation of {profile.topic}.",
                    "content_type": "definition",
                }
            ]
        )
        self._json(
            f"{project_id}/artifacts/retrieval/v{video_version}.json",
            [card.model_dump() for card in cards],
        )
        events.append("retrieval.complete")
        await self._notify_progress("retrieval.complete")
        collected = list(retrieved_chunks or [])
        if paper_outline is not None:
            curriculum = curriculum_from_outline(
                paper_outline,
                minutes=duration_minutes,
                source_ids=[source for card in cards for source in card.source_ids],
            )
        else:
            curriculum = await self._curated_curriculum(
                project_id, video_version, request, profile, collected, resume
            ) or build_curriculum(profile, cards)
        self._json(f"{project_id}/artifacts/curriculum/v{video_version}.json", curriculum)
        events.append("curriculum.complete")
        await self._notify_progress(
            "curriculum.complete",
            detail={
                "plan": curriculum.kind,
                "sections": len(curriculum.items),
                "references": len(curriculum.references),
            },
        )
        dossier = build_teaching_dossier(profile, curriculum, cards)
        if paper_outline is not None:
            # The outline is compact, structured grounding for the whole paper.
            dossier["evidence"] = outline_evidence(paper_outline)
        elif curriculum.kind == "curated":
            # Later stages work from the curator's plan, not the raw material.
            dossier["evidence"] = plan_evidence(curriculum)
        lookup = self._reference_lookup(curriculum, collected)
        self._json(f"{project_id}/artifacts/pedagogy/v{video_version}.json", dossier)
        saved_script = (
            self._read_json(f"{project_id}/artifacts/script/v{video_version}.json")
            if resume
            else None
        )
        if saved_script:
            blocks = [NarrationBlock.model_validate(block) for block in saved_script]
            dossier["script_review"] = self._read_json(
                f"{project_id}/artifacts/script_review/v{video_version}.json"
            )
        else:
            script_skills = await self.skills.context_for("research")
            script_terms = set(re.findall(r"[a-z]+", f"{request} {profile.topic}".lower()))
            script_skills = "\n\n".join([script_skills, *self._corrections("script", script_terms)])
            with (
                usage_stage("script"),
                call_timeout(getattr(self, "long_call_timeout_seconds", 420)),
            ):
                blocks = await write_script(
                    self.llm,
                    curriculum,
                    dossier,
                    skill_context=script_skills,
                    learner_request=request,
                    learner_level=profile.target_level,
                    review_callback=self._report_script_review,
                    source_scope=self._source_scope,
                    lookup=lookup,
                )
        if dossier.get("script_review"):
            self._json(
                f"{project_id}/artifacts/script_review/v{video_version}.json",
                dossier["script_review"],
            )
        self._json(
            f"{project_id}/artifacts/script/v{video_version}.json",
            [block.model_dump() for block in blocks],
        )
        events.append("script.complete")
        review = dossier.get("script_review") or {}
        await self._notify_progress(
            "script.complete",
            detail={
                "segments": len(blocks),
                "revisions": review.get("revision_count", 0),
                "checks": review.get("checks", []),
                "resumed": bool(saved_script),
            },
        )
        saved_storyboard = (
            self._read_json(f"{project_id}/artifacts/storyboard/v{video_version}.json")
            if saved_script
            else None
        )
        if saved_storyboard and set(saved_storyboard) == {block.block_id for block in blocks}:
            storyboard = {
                key: [VisualBeat.model_validate(beat) for beat in beats]
                for key, beats in saved_storyboard.items()
            }
        else:
            section_notes = {
                f"C{index:02d}": "; ".join(
                    [*item.key_points, *(f"show: {idea}" for idea in item.visual_ideas)]
                )
                for index, item in enumerate(curriculum.items, 1)
                if curriculum.kind == "curated" and (item.key_points or item.visual_ideas)
            }
            looked_up = (
                await lookup(
                    "plan the visuals for this lesson",
                    "\n".join(f"{key}: {notes}" for key, notes in section_notes.items()),
                )
                if section_notes
                else ""
            )
            with (
                usage_stage("storyboard"),
                call_timeout(getattr(self, "long_call_timeout_seconds", 420)),
            ):
                storyboard = await plan_storyboard(
                    self._planner(), blocks, section_notes=section_notes, looked_up=looked_up
                )
        self._json(
            f"{project_id}/artifacts/storyboard/v{video_version}.json",
            {key: [beat.model_dump() for beat in beats] for key, beats in storyboard.items()},
        )
        events.append("storyboard.complete")
        await self._notify_progress("storyboard.complete")
        specs = compile_scene_specs(blocks, storyboard)
        if scene_version_base:
            specs = [spec.model_copy(update={"version": scene_version_base + 1}) for spec in specs]
        events.append("scenes.created")
        await self._notify_progress(
            "scenes.created",
            detail={
                "scene_count": len(specs),
                "scenes": [f"{spec.scene_id}: {spec.title}" for spec in specs],
            },
        )
        with usage_stage("narration"):
            await self._synthesize_lesson_narration(project_id, specs)
        # The session-backed correction/skill queries happen before fan-out.
        # SQLAlchemy AsyncSession is not safe for concurrent use; scene tasks
        # themselves are now independent and can generate, render, and verify
        # while the scheduler immediately fills every free scene slot.
        all_tags = sorted({tag for spec in specs for tag in spec.tags})
        all_primitives = sorted({beat.primitive for spec in specs for beat in spec.visual_beats})
        shared_corrections = await self.corrections.retrieve(
            tags=all_tags, primitives=all_primitives, limit=20
        )
        code_skill_context = await self.skills.context_for("code")
        results = await self._run_scenes_as_ready(
            project_id,
            specs,
            events,
            known_source_ids={source for card in cards for source in card.source_ids},
            corrections=shared_corrections,
            code_skill_context=code_skill_context,
        )
        scenes = [result for result in results if isinstance(result, ProducedScene)]
        blocked_scenes = [
            {
                "scene_id": spec.scene_id,
                "title": spec.title,
                "error": f"{type(result).__name__}: {result}",
            }
            for spec, result in zip(specs, results)
            if not isinstance(result, ProducedScene)
        ]
        for blocked in blocked_scenes:
            events.append(f"scene.blocked:{blocked['scene_id']}")
            await self._notify_progress(
                "scene.blocked", blocked["scene_id"], {"reasons": [blocked["error"][:400]]}
            )
        _reject_empty_lesson(scenes, blocked_scenes)
        voices = {
            (scene.audio.provider, scene.audio.model, scene.audio.voice_id)
            for scene in scenes
            if scene.audio.voice_id
        }
        if len(voices) > 1:
            raise RuntimeError(
                "Narrator identity differs across scenes; regenerate with the pinned project voice"
            )
        # Review the finished lesson before it is assembled, so a scene whose
        # narration is wrong for the lesson is rebuilt rather than only noted.
        lesson_review = await self._review_lesson_flow(scenes)
        repairs = _scenes_to_repair(lesson_review, scenes)
        if repairs:
            scenes, repaired = await self._repair_scenes(
                project_id,
                scenes,
                repairs,
                events,
                known_source_ids={source for card in cards for source in card.source_ids},
                code_skill_context=code_skill_context,
            )
            if repaired:
                # One repair round only; what remains ships as review notes.
                lesson_review = {
                    **await self._review_lesson_flow(scenes),
                    "repaired_scenes": repaired,
                }
        events.append("video.assembling")
        await self._notify_progress("video.assembling")
        video_key = f"{project_id}/video/v{video_version}/final.mp4"
        video_path = self.storage.local_path(video_key)
        assembly = self.assembler.assemble(
            [scene.render_path for scene in scenes],
            video_path,
            audio_paths=[scene.audio.audio_path for scene in scenes],
        )
        if not assembly.success:
            raise RuntimeError(f"video assembly failed: {assembly.log[-1000:]}")
        if hasattr(self.assembler, "finalize_ending") and self.assembler.finalize_ending(
            video_path
        ):
            await self._notify_progress(
                "video.ending", detail={"reason": "Held the closing frame and faded out."}
            )
        self._json(f"{project_id}/video/v{video_version}/lesson_review.json", lesson_review)
        await self._notify_progress("lesson.reviewed", detail=lesson_review)
        timeline = build_timeline(
            [
                (
                    scene.spec.scene_id,
                    media_duration(scene.audio.duration_seconds, scene.video_duration_seconds),
                    scene.render_version,
                    scene.render_path,
                )
                for scene in scenes
            ]
        )
        self._json(f"{project_id}/video/v{video_version}/timeline.json", timeline)
        cues = merge_scene_cues(
            [
                (segment.start, scene.audio.sentence_timings)
                for segment, scene in zip(timeline.scenes, scenes)
            ]
        )
        srt_key, vtt_key = (
            f"{project_id}/video/v{video_version}/subtitles.srt",
            f"{project_id}/video/v{video_version}/subtitles.vtt",
        )
        self.storage.put_bytes(srt_key, cues_to_srt(cues).encode(), overwrite=True)
        self.storage.put_bytes(vtt_key, cues_to_vtt(cues).encode(), overwrite=True)
        events.append("video.complete")
        await self._notify_progress("video.complete")
        self._json(
            f"{project_id}/artifacts/build_state.json", {"request": request, "complete": True}
        )
        return BuildResult(
            project_id,
            scenes,
            str(video_path),
            timeline,
            str(self.storage.local_path(srt_key)),
            str(self.storage.local_path(vtt_key)),
            events,
            blocked_scenes,
            lesson_review,
        )

    async def _run_scenes_as_ready(
        self,
        project_id: str,
        specs: list[SceneSpec],
        events: list[str],
        *,
        known_source_ids: set[str],
        corrections,
        code_skill_context: str,
    ) -> list[ProducedScene | Exception]:
        """Bounded work-conserving scene scheduler.

        A scene includes its code/audio generation plus render/verification and
        repair loop. As soon as any full scene finishes, the next planned scene
        starts; there are no batches that wait for a slower sibling. This also
        means completed scenes enter verification while other scenes are still
        generating their code or audio.
        """
        results: list[ProducedScene | Exception | None] = [None] * len(specs)
        next_index = 0
        running: dict[asyncio.Task, int] = {}

        def start(index: int) -> None:
            running[
                asyncio.create_task(
                    self.produce_scene(
                        project_id,
                        specs[index],
                        events,
                        known_source_ids=known_source_ids,
                        prefetched_corrections=corrections,
                        code_skill_context=code_skill_context,
                    )
                )
            ] = index

        while next_index < len(specs) and len(running) < self.max_concurrent_scenes:
            start(next_index)
            next_index += 1
        while running:
            completed, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                index = running.pop(task)
                try:
                    results[index] = task.result()
                except Exception as error:  # noqa: BLE001 - retain per-scene failure details
                    results[index] = error
                if next_index < len(specs):
                    start(next_index)
                    next_index += 1
        return [result for result in results if result is not None]

    async def produce_scene(
        self,
        project_id: str,
        spec: SceneSpec,
        events: list[str] | None = None,
        *,
        artifact_version: int | None = None,
        persist_spec: bool = True,
        existing_audio: TTSResult | None = None,
        known_source_ids: set[str] | None = None,
        prefetched_corrections=None,
        code_skill_context: str | None = None,
        edit_request: str = "",
    ) -> ProducedScene:
        # Each scene runs in its own task, so this labels only this scene's calls.
        set_usage_scene(spec.scene_id)
        events = events if events is not None else []
        base = f"{project_id}/scenes/{spec.scene_id}"
        artifact_version = artifact_version or spec.version
        events.append("scene.started")
        await self._notify_progress("scene.started", spec.scene_id)
        if getattr(self, "resume", False) and existing_audio is None:
            finished = self._resume_scene(project_id, spec)
            if finished is not None:
                getattr(self, "_lesson_audio", {}).pop(spec.scene_id, None)
                events.append("scene.complete")
                await self._notify_progress(
                    "scene.complete",
                    spec.scene_id,
                    {"resumed": True, "notes": _issue_reasons(finished.report)},
                )
                return finished
        display_spec, spoken_narration = await self._localized_presentation(spec)
        if persist_spec:
            self._json(f"{base}/spec/v{spec.version}.json", spec)
        relevant = prefetched_corrections
        if relevant is None and getattr(self, "correction_folder", None) is None:
            relevant = await self.corrections.retrieve(
                tags=spec.tags, primitives=[beat.primitive for beat in spec.visual_beats]
            )
        # An edited scene must also show the change the learner asked for.
        review_extra = (edit_check(edit_request),) if edit_request.strip() else ()
        # What the correction notes are matched against: the kind of scene.
        scene_terms = {
            *spec.tags,
            *(beat.primitive for beat in spec.visual_beats),
            *(["data_plot"] if spec.data_plots else []),
            *(["paper_figure"] if spec.paper_figures else []),
        }
        events.extend(["scene.code_started", "scene.audio_started"])
        await self._notify_progress("scene.code_started", spec.scene_id)
        await self._notify_progress("scene.audio_started", spec.scene_id)
        code_skills = code_skill_context or await self.skills.context_for("code")
        if existing_audio is None:
            audio = getattr(self, "_lesson_audio", {}).pop(spec.scene_id, None)
            if audio is None:
                audio_path = self.storage.local_path(f"{base}/audio/v{artifact_version}.wav")
                said = respell(spoken_narration, spec.pronunciations)
                audio = await self.tts.synthesize(said, audio_path, language=self.voice_language)
                audio = restore_spelling(
                    await self._measure_timings(audio, said), spec.pronunciations
                )
            # Narration is never time-stretched to fit the planner's estimate:
            # per-scene tempo changes made one narrator sound like several.
            # The animation is timed to the measured speech instead.
        else:
            audio = existing_audio
        if existing_audio is None and spec.pauses:
            # Reused audio already has its pauses; a fresh recording gets them now.
            audio = insert_pauses(
                audio,
                map_pauses(
                    spec.pauses, len(sentences(spec.narration)), len(audio.sentence_timings)
                ),
                self.storage.local_path(f"{base}/audio/v{artifact_version}-paused.wav"),
            )
        # Generate against measured speech, not a guessed word rate. This also
        # keeps each visual cue anchored to the narration actually synthesized.
        display_spec = display_spec.model_copy(
            update={
                "duration_target_seconds": audio.duration_seconds,
                "visual_beats": align_visual_beats(
                    spoken_narration,
                    display_spec.visual_beats,
                    audio.duration_seconds,
                    word_timings=audio.word_timings,
                ),
            }
        )
        located = await self._locate_figure_parts(spec)
        if located is not spec:
            # Both copies carry the boxes, so a re-planned repair keeps them.
            spec = located
            display_spec = display_spec.model_copy(update={"paper_figures": spec.paper_figures})
        code_model = getattr(self.code_llm, "model", "") or "code model"
        try:
            generated = await generate_scene_code(
                self.code_llm,
                display_spec,
                self._corrections("code", scene_terms, relevant),
                skill_context=code_skills,
                narration=spoken_narration,
                sentence_durations=[cue.end - cue.start for cue in audio.sentence_timings],
                edit_request=edit_request,
            )
        except CodeGenerationError:
            # The cheap model is unavailable: the stronger one writes the scene.
            stronger = getattr(self, "code_escalation_llm", None)
            if stronger is None:
                raise
            generated = await generate_scene_code(
                stronger,
                display_spec,
                self._corrections("code", scene_terms, relevant),
                skill_context=code_skills,
                narration=spoken_narration,
                sentence_durations=[cue.end - cue.start for cue in audio.sentence_timings],
                edit_request=edit_request,
            )
            code_model = getattr(stronger, "model", "") or "code model"
        attempt_log: list[str] = []
        # Problems earlier attempts failed on; those a later attempt fixed are learned.
        failed_issues: dict[str, VerificationIssue] = {}
        best_attempt: dict | None = None
        fallback_rank = best_any_rank = None
        code_key = f"{base}/code/v{artifact_version}.py"
        code_version = artifact_version
        if existing_audio is None:
            self._json(f"{base}/audio/v{artifact_version}.json", audio)
        timing = reconcile_timing(spec.duration_target_seconds, audio.duration_seconds)
        self._json(f"{base}/timing/v{artifact_version}.json", timing.__dict__)
        # The initial request already used actual audio duration and cue lengths.
        # A difference from the requested lesson budget is not a reason to make
        # a second identical model request before even rendering the first one.
        self.storage.put_bytes(code_key, generated.python_code.encode(), overwrite=True)
        feedback: list[str] = []
        report: VerificationReport | None = None
        render_path = ""
        # Best attempt that actually produced a video, kept so a scene whose only
        # remaining faults are cosmetic is not discarded along with its narration.
        fallback: tuple[str, VerificationReport, int, int, float | None] | None = None
        # Best rendered attempt of any quality: shipped (marked degraded) only
        # when neither a cosmetic-only attempt nor the safe scene is available.
        best_any: tuple[str, VerificationReport, int, int, float | None] | None = None
        next_render_version = artifact_version
        timing_repaired = False
        for attempt in range(self.manager.max_repair_attempts + 1):
            render_version = next_render_version
            next_render_version += 1
            # Stills the video reviewer saw, and its checklist scores, for this render.
            review_frames: list[bytes] = []
            checklist_scores: list[dict] = []
            events.append("scene.render_started")
            await self._notify_progress("scene.render_started", spec.scene_id)
            render_dir = self.storage.local_path(f"{base}/render/v{render_version}")
            result = await asyncio.to_thread(
                self.renderer.render,
                generated.python_code,
                generated.scene_class,
                render_dir,
                **_render_assets(spec),
            )
            video_duration = (
                self.assembler.probe_duration(result.video_path) if result.video_path else None
            )
            # Manim animation time and narration time are independently produced.
            # Reconcile them using the measured render duration before accepting
            # the scene; otherwise muxing freezes the last frame or cuts narration.
            if (
                result.success
                and result.video_path
                and video_duration is not None
                and audio.duration_seconds > 0
                and abs(audio.duration_seconds - video_duration)
                > max(1.0, audio.duration_seconds * 0.08)
                and not timing_repaired
            ):
                ratio = audio.duration_seconds / video_duration
                retimed = scale_manim_durations(generated.python_code, ratio)
                if retimed != generated.python_code:
                    timing_repaired = True
                    code_version += 1
                    generated = generated.model_copy(update={"python_code": retimed})
                    code_key = f"{base}/code/v{code_version}.py"
                    self.storage.put_bytes(code_key, retimed.encode(), overwrite=True)
                    feedback.append(
                        "The measured animation duration was "
                        f"{video_duration:.2f}s while the narration is "
                        f"{audio.duration_seconds:.2f}s. Animation and narration must end "
                        "together; keep every visual visible for the narration that explains it."
                    )
                    events.append("scene.timing_repaired")
                    await self._notify_progress("scene.timing_repaired", spec.scene_id)
                    result = await asyncio.to_thread(
                        self.renderer.render,
                        generated.python_code,
                        generated.scene_class,
                        self.storage.local_path(f"{base}/render/v{next_render_version}"),
                        **_render_assets(spec),
                    )
                    render_version = next_render_version
                    next_render_version += 1
                    video_duration = (
                        self.assembler.probe_duration(result.video_path)
                        if result.video_path
                        else None
                    )
            events.append("scene.verifying")
            await self._notify_progress("scene.verifying", spec.scene_id)
            layers = {
                "render": verify_render(spec.scene_id, result),
                "pedagogy": [
                    *verify_pedagogy(display_spec),
                    *verify_rendered_storyboard(
                        display_spec,
                        generated.python_code,
                        allow_placeholder=getattr(self.code_llm, "name", "") == "mock",
                    ),
                ],
                "raw_latex": verify_no_raw_latex_text(spec.scene_id, generated.python_code),
                "math": verify_required_equations(
                    spec.scene_id, spec.equations, generated.python_code
                ),
                "semantic": verify_source_grounding(display_spec, known_source_ids or set()),
                "figures": verify_paper_figures_shown(spec, generated.python_code),
                "cue_timing": verify_cue_overflow(
                    spec.scene_id,
                    _read_log(result.log_path),
                    len(audio.sentence_timings) or 1,
                ),
            }
            folder = getattr(self, "correction_folder", None)
            if folder is not None:
                # Checks written for lessons learned (correction_folder/code/checks).
                layers["corrections"] = [
                    VerificationIssue(
                        scene_id=spec.scene_id,
                        category=f"correction.{entry}",
                        severity="high",
                        confidence=0.9,
                        description=finding,
                        suggested_repair=f"Follow correction {entry}.",
                    )
                    for entry, finding in folder.run_checks("code", generated.python_code)
                ]
            # Geometry measurements are hints for the repair prompt, not a verdict:
            # whether a scene looks right is decided by the video reviewer, which
            # watches the whole scene with its narration.
            geometry_hints = _verify_render_layout(spec.scene_id, result)
            if getattr(self, "static_layout_gate", False) or self.video_verifier is None:
                layers["layout"] = geometry_hints
                geometry_hints = []
            if self.video_verifier is None and self.vision is not None:
                # Keyframe-only review is the fallback when no video model exists.
                layers["vision"] = await verify_visual(
                    self.vision, display_spec, result.keyframe_paths
                )
            if result.success and result.video_path and self.video_verifier is not None:
                try:
                    qa_path = self.storage.local_path(
                        f"{base}/render/v{render_version}/video-qa.mp4"
                    )
                    qa_result = self.assembler.assemble(
                        [result.video_path], qa_path, audio_paths=[audio.audio_path]
                    )
                    if not qa_result.success:
                        raise RuntimeError(
                            f"could not mux scene video and narration: {qa_result.log[-500:]}"
                        )
                    base_prompt = scene_review_prompt(
                        objective=display_spec.learning_objective,
                        tags=display_spec.tags,
                        narration=spoken_narration,
                        cues=review_cues(audio.sentence_timings, spec.pauses, spec.narration),
                        source_code=generated.python_code,
                        notes="\n".join(self._corrections("review", scene_terms)),
                        extra=review_extra,
                        measured=measured_overlaps(spec.scene_id, result.bounds_path),
                    )
                    qa_duration = self.assembler.probe_duration(str(qa_path)) or max(
                        video_duration or 0, audio.duration_seconds
                    )
                    frame_only_review = bool(
                        getattr(self.video_verifier, "frame_only_video_review", False)
                    )
                    # Review the whole scene in one pass so the model judges whether
                    # it makes sense as a whole; only very long scenes are sampled.
                    whole_scene = frame_only_review or qa_duration <= WHOLE_SCENE_REVIEW_SECONDS
                    excerpt_seconds = qa_duration if whole_scene else 8.0
                    last_start = max(0.0, qa_duration - excerpt_seconds)
                    if frame_only_review or qa_duration <= excerpt_seconds:
                        excerpt_starts = [0.0]
                    elif qa_duration < 2 * excerpt_seconds:
                        excerpt_starts = [0.0, last_start]
                    else:
                        excerpt_starts = [0.0, last_start / 2, last_start]
                    excerpt_starts = list(
                        dict.fromkeys(round(start, 2) for start in excerpt_starts)
                    )
                    layers["video"] = []
                    measured_fallback_schedule = "simplified" in spec.tags and (
                        _scheduled_fallback_matches_audio(
                            generated.python_code, audio.sentence_timings
                        )
                    )
                    with tempfile.TemporaryDirectory(prefix="scene-av-review-") as review_dir:
                        for sample_index, offset in enumerate(excerpt_starts):
                            clip_video = Path(review_dir) / f"sample-{sample_index}.mp4"
                            clip_audio = Path(review_dir) / f"sample-{sample_index}.wav"
                            if not self.assembler.extract_review_excerpt(
                                qa_path,
                                audio.audio_path,
                                offset,
                                excerpt_seconds,
                                clip_video,
                                clip_audio,
                            ):
                                raise RuntimeError(
                                    "could not extract synchronized AV review sample"
                                )
                            frame_times = (
                                [qa_duration * index / 7 for index in range(7)]
                                + [max(0.0, qa_duration - 0.25)]
                                if whole_scene
                                else [
                                    min(0.5, excerpt_seconds / 4),
                                    excerpt_seconds / 2,
                                    max(0, excerpt_seconds - 0.5),
                                ]
                            )
                            sample_keyframes = self.assembler.extract_review_keyframes(
                                clip_video,
                                frame_times,
                                Path(review_dir) / f"sample-{sample_index}-frames",
                            )
                            prompt = (
                                base_prompt
                                + (
                                    "This is the deterministic sentence-card fallback. Its pipe and "
                                    "moving water marker are a persistent analogy for the current, "
                                    "voltage, and resistance being discussed. Do not call that visual "
                                    "irrelevant solely because it persists across adjacent narration "
                                    "cues; report a mismatch only if the visible content is inaccurate, "
                                    "obscures the explanation, or fails to show a claimed change.\n"
                                    if measured_fallback_schedule
                                    else ""
                                )
                                + f"\nThis synchronized video/audio excerpt covers scene time "
                                f"{offset:.1f}–{offset + excerpt_seconds:.1f}s. Timestamp your findings "
                                "relative to this excerpt; keep every timestamp within its duration. "
                                "The still images are sampled from this exact excerpt in order."
                            )
                            qa = await self.video_verifier.analyze_video(
                                prompt,
                                clip_video.read_bytes(),
                                clip_audio.read_bytes(),
                                keyframes=[path.read_bytes() for path in sample_keyframes],
                                timestamp_offset_seconds=offset,
                                excerpt_duration_seconds=excerpt_seconds,
                            )
                            review_frames = [path.read_bytes() for path in sample_keyframes]
                            qa_issues = qa.get("issues", [])
                            answers = qa.get("checklist") or []
                            # The deterministic fallback has one card transition per
                            # measured TTS cue. If its schedule matches the WAV exactly,
                            # use measured cues for timing and retain semantic, overlap,
                            # and readability findings from the video reviewer.
                            if measured_fallback_schedule:
                                qa_issues = _fallback_video_findings(qa_issues)
                                answers = [a for a in answers if a["id"] != "audio_video_sync"]
                            layers["video"].extend(
                                VerificationIssue(scene_id=spec.scene_id, **issue)
                                for issue in qa_issues
                            )
                            if answers:
                                checked = score_checklist(spec.scene_id, answers, review_extra)
                                layers["video"].extend(checked.issues)
                                checklist_scores.append(
                                    {
                                        "excerpt_start": offset,
                                        "score": round(checked.score, 3),
                                        "passed": checked.passed,
                                        "failed": checked.failed,
                                    }
                                )
                            if qa.get("makes_sense") is False and not any(
                                issue.severity in {"high", "critical"} for issue in layers["video"]
                            ):
                                layers["video"].append(
                                    VerificationIssue(
                                        scene_id=spec.scene_id,
                                        category="video.does_not_make_sense",
                                        severity="high",
                                        confidence=0.9,
                                        description=str(
                                            qa.get("summary")
                                            or "The reviewer judged that the scene does not make sense."
                                        )[:600],
                                        suggested_repair=(
                                            "Rework the scene so each narration sentence is shown "
                                            "clearly, legibly and without clutter."
                                        ),
                                    )
                                )
                except Exception as error:  # noqa: BLE001 - must fail closed across SDK exceptions
                    layers["video"] = [
                        VerificationIssue(
                            scene_id=spec.scene_id,
                            category="video.verification_unavailable",
                            severity="high",
                            confidence=1.0,
                            description=f"Audio/video verification failed: {type(error).__name__}: {error}",
                            suggested_repair="Retry the audio/video quality check before publishing this scene.",
                        )
                    ]
            if video_duration is not None and audio.duration_seconds > 0:
                drift = abs(audio.duration_seconds - video_duration)
                if drift > max(1.0, audio.duration_seconds * 0.08):
                    layers["audio_video_timing"] = [
                        VerificationIssue(
                            scene_id=spec.scene_id,
                            category="video.audio_video_duration_mismatch",
                            severity="high",
                            confidence=1.0,
                            description=(
                                f"Visual duration {video_duration:.2f}s differs from narration "
                                f"duration {audio.duration_seconds:.2f}s by {drift:.2f}s."
                            ),
                            suggested_repair="Retarget scene play/wait durations to the narration and rerender.",
                        )
                    ]
            layers = _drop_stale_prompt_title_findings(layers, generated.python_code)
            report = aggregate_reports(
                spec.scene_id,
                layers,
            )
            self._json(f"{base}/verification/v{render_version}.json", report)
            if checklist_scores:
                self._json(
                    f"{base}/verification/v{render_version}-checklist.json", checklist_scores
                )
            checklist_score = (
                min(entry["score"] for entry in checklist_scores) if checklist_scores else None
            )
            off_frame = sum(
                issue.category == "layout.out_of_frame"
                for issue in [*geometry_hints, *layers.get("layout", [])]
            )
            rank = _attempt_rank(report, checklist_score, off_frame)
            # When every attempt fails, ship the best one, not the latest.
            if (
                result.video_path
                and _only_cosmetic(report)
                and (fallback is None or rank < fallback_rank)
            ):
                fallback = (result.video_path, report, render_version, code_version, video_duration)
                fallback_rank = rank
            if result.video_path and (best_any is None or rank < best_any_rank):
                best_any = (result.video_path, report, render_version, code_version, video_duration)
                best_any_rank = rank
            # Repairs work from the best attempt so far, so a repair that made
            # things worse does not become the starting point of the next one.
            if best_attempt is None or rank < best_attempt["rank"]:
                best_attempt = {
                    "rank": rank,
                    "code": generated.python_code,
                    "report": report,
                    "hints": geometry_hints,
                    "frames": review_frames,
                    "render_version": render_version,
                    "checklist_score": checklist_score,
                    "model": code_model,
                }
            if not report.passed:
                for issue in report.issues:
                    if issue.severity in {"high", "critical"}:
                        failed_issues.setdefault(issue.category, issue)
            if report.passed and result.video_path:
                self._learn_from_repairs(project_id, spec.scene_id, failed_issues, report)
                render_path = result.video_path
                events.append("scene.complete")
                await self._notify_progress(
                    "scene.complete",
                    spec.scene_id,
                    {
                        "attempt": attempt + 1,
                        "checks": sorted(name for name, ok in report.layers.items() if ok),
                        "notes": _issue_reasons(report),
                    },
                )
                audio_version = (
                    artifact_version if existing_audio is None else max(1, artifact_version - 1)
                )
                produced = ProducedScene(
                    spec,
                    str(self.storage.local_path(code_key)),
                    audio,
                    render_path,
                    report,
                    render_version,
                    code_version,
                    audio_version,
                    video_duration,
                )
                self._save_scene_result(project_id, produced)
                return produced
            action = self.manager.repair_action(attempt + 1)
            await self._notify_progress(
                "scene.rejected",
                spec.scene_id,
                {
                    "attempt": attempt + 1,
                    "max_attempts": self.manager.max_repair_attempts + 1,
                    "reasons": _issue_reasons(report)
                    or ([result.error[:300]] if result.error else ["render failed"]),
                    "next_action": action.value,
                },
            )
            if action == RepairAction.BLOCK:
                break
            events.append("scene.repairing")
            await self._notify_progress(
                "scene.repairing", spec.scene_id, {"action": action.value, "attempt": attempt + 2}
            )
            best = best_attempt
            feedback = _grouped_repair_feedback(best["report"].issues)
            if best["hints"]:
                feedback.append(
                    "Geometry measurements (hints only; fix if they match what the reviewer saw):\n"
                    + "\n".join(_grouped_repair_feedback(best["hints"]))
                )
            if action in {RepairAction.REVISE_SPEC, RepairAction.SIMPLIFY}:
                # Simplify the drawing, not the narrated sequence: dropping the
                # final beat removed the takeaway while its audio kept playing.
                beats = spec.visual_beats
                if action == RepairAction.SIMPLIFY:
                    feedback.append(
                        "Simplify to one central diagram and a separate equation band, updated "
                        "progressively. Preserve every narrated concept and the final takeaway; "
                        "remove decorative plots, duplicate labels and unnecessary transitions."
                    )
                spec = spec.model_copy(
                    update={
                        "version": spec.version + 1,
                        "visual_beats": beats,
                        # The final deterministic card scene is driven by the
                        # actual spoken sentences. Match its target to TTS so
                        # short voiceovers do not inherit the longer planner
                        # estimate and fail the AV duration gate.
                        "duration_target_seconds": (
                            audio.duration_seconds
                            if action == RepairAction.SIMPLIFY
                            else spec.duration_target_seconds
                        ),
                        "tags": sorted(set(spec.tags + ["simplified"])),
                    }
                )
                display_spec, spoken_narration = await self._localized_presentation(spec)
                self._json(f"{base}/spec/v{spec.version}.json", spec)
            failures = attempt + 1
            attempt_log.append(
                f"Attempt {failures} ({code_model}): "
                + ("; ".join(_issue_reasons(report)) or (result.error or "render failed")[:200])
            )
            if len(attempt_log) > 1:
                feedback.append(
                    "EARLIER ATTEMPTS (each was rejected for these reasons; do not repeat a fix "
                    "that already failed):\n" + "\n".join(attempt_log)
                )
            # The cheap model writes and repairs first. After it has failed
            # `escalate_after` times the stronger model takes over: it gets the
            # same brief, the best version so far with its score and findings,
            # and the stills the reviewer saw, and may reuse or rewrite any of it.
            stronger = getattr(self, "code_escalation_llm", None)
            escalate_after = getattr(self, "escalate_after_failures", 2)
            escalated = stronger is not None and failures >= escalate_after
            failed_checks = sorted(
                {
                    issue.category.removeprefix("video.checklist.")
                    for issue in best["report"].issues
                    if issue.severity in {"high", "critical"}
                }
            )
            best_note = (
                f"render {best['render_version']} by {best['model']}"
                + (
                    f", review checklist {best['checklist_score']:.0%} (90% passes)"
                    if best["checklist_score"] is not None
                    else ""
                )
                + (f"; blocking findings: {', '.join(failed_checks)}" if failed_checks else "")
            )
            if escalated and failures == escalate_after:
                await self._notify_progress(
                    "scene.escalated",
                    spec.scene_id,
                    {"model": getattr(stronger, "model", ""), "after_attempts": failures},
                )
            providers = [stronger] if escalated else [self.code_llm, stronger]
            code_error: Exception | None = None
            for provider in [candidate for candidate in providers if candidate is not None]:
                try:
                    generated = await generate_scene_code(
                        provider,
                        display_spec,
                        self._corrections(
                            "code",
                            scene_terms | {issue.category for issue in best["report"].issues},
                            relevant,
                        ),
                        feedback,
                        skill_context=code_skills,
                        narration=spoken_narration,
                        fallback_only=(
                            action == RepairAction.SIMPLIFY
                            and getattr(provider, "name", "") == "mock"
                        ),
                        # Deterministic scene templates must follow the measured audio
                        # on every repair attempt, including REVISE_SPEC. Otherwise a
                        # repair silently restores the planner's longer duration and
                        # reintroduces audio/video drift.
                        sentence_durations=[cue.end - cue.start for cue in audio.sentence_timings],
                        previous_code=best["code"],
                        previous_code_note=best_note,
                        edit_request=edit_request,
                        review_frames=best["frames"] if escalated else None,
                    )
                    code_model = getattr(provider, "model", "") or "code model"
                    break
                except CodeGenerationError as error:
                    # The cheap model being unavailable is a reason to try the
                    # stronger one, not to give up on the scene.
                    code_error = error
            else:
                await self._notify_progress(
                    "scene.code_failed", spec.scene_id, {"reasons": [str(code_error)[:300]]}
                )
                break
            code_version += 1
            code_key = f"{base}/code/v{code_version}.py"
            self.storage.put_bytes(code_key, generated.python_code.encode(), overwrite=True)
        if fallback is not None:
            # Repairs ran out, but a rendered scene whose only faults are layout
            # placement still teaches its point — and dropping it would silently
            # delete that section's narration and audio from the lesson. Ship it
            # and record the degradation instead.
            (
                fallback_path,
                fallback_report,
                fallback_version,
                fallback_code_version,
                fallback_duration,
            ) = fallback
            events.append(f"scene.degraded:{spec.scene_id}")
            await self._notify_progress(
                "scene.degraded", spec.scene_id, {"reasons": _issue_reasons(fallback_report)}
            )
            produced = ProducedScene(
                spec,
                str(self.storage.local_path(f"{base}/code/v{fallback_code_version}.py")),
                audio,
                fallback_path,
                fallback_report,
                fallback_version,
                fallback_code_version,
                artifact_version if existing_audio is None else max(1, artifact_version - 1),
                fallback_duration,
            )
            self._save_scene_result(project_id, produced)
            return produced
        safe = await self._render_safe_scene(
            project_id, spec, display_spec, spoken_narration, audio, report, next_render_version
        )
        if safe is not None:
            self._save_scene_result(project_id, safe)
            return safe
        audio_version = artifact_version if existing_audio is None else max(1, artifact_version - 1)
        if best_any is not None:
            # Even the plain scene failed to render: the best real attempt is
            # still better than losing this part of the lesson.
            path, best_report, version, best_code_version, duration = best_any
            await self._notify_progress(
                "scene.degraded",
                spec.scene_id,
                {"reasons": ["best of the failed attempts", *_issue_reasons(best_report)]},
            )
            produced = ProducedScene(
                spec,
                str(self.storage.local_path(f"{base}/code/v{best_code_version}.py")),
                audio,
                path,
                best_report,
                version,
                best_code_version,
                audio_version,
                duration,
            )
            self._save_scene_result(project_id, produced)
            return produced
        still = self._still_scene(project_id, spec, audio, report, next_render_version + 1)
        if still is not None:
            still = replace(still, audio_version=audio_version)
            await self._notify_progress(
                "scene.degraded", spec.scene_id, {"reasons": _issue_reasons(still.report)}
            )
            self._save_scene_result(project_id, still)
            return still
        reasons = "; ".join(_issue_reasons(report)) if report else "unknown error"
        raise RuntimeError(f"{spec.scene_id} blocked after all repair attempts: {reasons}")

    def _corrections(self, agent: str, terms: set[str], fallback=None) -> list[str]:
        """The agent's lessons from earlier failures, as prompt text."""
        folder = getattr(self, "correction_folder", None)
        if folder is None:
            return [entry.prompt_fragment() for entry in fallback or []]
        text = folder.context(agent, terms)
        return [text] if text else []

    def _learn_from_repairs(
        self,
        project_id: str,
        scene_id: str,
        failed: dict[str, VerificationIssue],
        final: VerificationReport,
    ) -> None:
        """Record each problem an earlier attempt failed on and the passing one fixed."""
        folder = getattr(self, "correction_folder", None)
        if folder is None or not failed:
            return
        remaining = {issue.category for issue in final.issues if issue.severity != "low"}
        for category, issue in failed.items():
            if category in remaining or category in _NOT_LEARNABLE:
                continue
            key = category.removeprefix("video.checklist.").removeprefix("video.")
            promoted = folder.record(
                "code",
                key,
                issue.description,
                issue.suggested_repair,
                scene=f"{project_id}/{scene_id}",
            )
            if promoted is not None:
                logger.info("New code correction %s: %s", promoted.id, key)

    async def _locate_figure_parts(self, spec: SceneSpec) -> SceneSpec:
        """Box the parts of each paper figure this scene points at (once per figure)."""
        locator, backup = getattr(self, "figure_locators", None) or (None, None)
        missing = [figure for figure in spec.paper_figures if not figure.regions]
        if locator is None or not missing:
            return spec
        found = await asyncio.gather(
            *(locate_figure_regions(locator, backup, figure, spec.narration) for figure in missing)
        )
        regions = {figure.label: parts for figure, parts in zip(missing, found, strict=True)}
        return spec.model_copy(
            update={
                "paper_figures": [
                    figure.model_copy(update={"regions": regions[figure.label]})
                    if figure.label in regions
                    else figure
                    for figure in spec.paper_figures
                ]
            }
        )

    def _still_scene(
        self,
        project_id: str,
        spec: SceneSpec,
        audio: TTSResult,
        last_report: VerificationReport | None,
        render_version: int,
    ) -> ProducedScene | None:
        """Final fallback: the narration over a plain frame, made with FFmpeg only."""
        if not hasattr(self.assembler, "still_scene"):
            return None
        base = f"{project_id}/scenes/{spec.scene_id}"
        path = self.storage.local_path(f"{base}/render/v{render_version}/still.mp4")
        duration = audio.duration_seconds or spec.duration_target_seconds
        if not self.assembler.still_scene(path, duration):
            return None
        reasons = _issue_reasons(last_report) if last_report else ["the scene never rendered"]
        report = VerificationReport(
            scene_id=spec.scene_id,
            passed=False,
            score=0.1,
            issues=[
                VerificationIssue(
                    scene_id=spec.scene_id,
                    category="render.still_fallback",
                    severity="medium",
                    confidence=1.0,
                    description=(
                        "No animation could be rendered, so the narration plays over a plain "
                        "frame: " + "; ".join(reasons)[:600]
                    ),
                    suggested_repair="Regenerate this scene from the editor.",
                )
            ],
            layers={"render": True},
        )
        self._json(f"{base}/verification/v{render_version}.json", report)
        return ProducedScene(spec, "", audio, str(path), report, render_version, 0, 1, duration)

    async def _render_safe_scene(
        self,
        project_id: str,
        spec: SceneSpec,
        display_spec: SceneSpec,
        narration: str,
        audio: TTSResult,
        last_report: VerificationReport | None,
        render_version: int,
    ) -> ProducedScene | None:
        """Last resort for a scene that never rendered: a plain, verified layout.

        It shows the scene's title, equations and key labels while the
        narration plays, so a long lecture keeps its explanation instead of
        failing outright. The scene is reported as degraded with the reason.
        """
        base = f"{project_id}/scenes/{spec.scene_id}"
        try:
            generated = await generate_scene_code(
                self.code_llm,
                display_spec,
                narration=narration,
                fallback_only=True,
                sentence_durations=[cue.end - cue.start for cue in audio.sentence_timings],
            )
        except Exception as error:  # noqa: BLE001 - the original failure is reported instead
            logger.warning(
                "Safe fallback scene for %s failed to generate: %s", spec.scene_id, error
            )
            return None
        code_key = f"{base}/code/safe.py"
        self.storage.put_bytes(code_key, generated.python_code.encode(), overwrite=True)
        result = await asyncio.to_thread(
            self.renderer.render,
            generated.python_code,
            generated.scene_class,
            self.storage.local_path(f"{base}/render/v{render_version}"),
        )
        if not result.success or not result.video_path:
            return None
        reasons = _issue_reasons(last_report) if last_report else ["the scene never rendered"]
        report = VerificationReport(
            scene_id=spec.scene_id,
            passed=False,
            score=0.3,
            issues=[
                VerificationIssue(
                    scene_id=spec.scene_id,
                    category="render.safe_fallback",
                    severity="medium",
                    confidence=1.0,
                    description=(
                        "Shown as a simple title-and-equation scene because every generated "
                        "animation failed: " + "; ".join(reasons)[:600]
                    ),
                    suggested_repair="Regenerate this scene from the editor.",
                )
            ],
            layers={"render": True},
        )
        self._json(f"{base}/verification/v{render_version}.json", report)
        await self._notify_progress(
            "scene.degraded", spec.scene_id, {"reasons": _issue_reasons(report)}
        )
        return ProducedScene(
            spec,
            str(self.storage.local_path(code_key)),
            audio,
            result.video_path,
            report,
            render_version,
            0,
            spec.version,
            self.assembler.probe_duration(result.video_path),
        )

    async def _report_script_review(self, review_round: int, review) -> None:
        await self._notify_progress(
            "script.review",
            detail={
                "round": review_round,
                "approved": review.approved,
                "blocking": review.blocking,
                "problems": [problem[:240] for problem in review.problems[:4]],
            },
        )

    async def _review_lesson_flow(self, scenes: list[ProducedScene]) -> dict:
        """Whole-lesson check: scenes flow into each other and the ending resolves.

        Per-scene reviewers only see one scene. This pass reads the finished
        lesson in order, with what each scene shows, so a missing transition or
        a dangling ending is reported to the learner rather than hidden.
        """
        summary = {
            "scene_count": len(scenes),
            "duration_seconds": round(sum(scene.audio.duration_seconds for scene in scenes), 1),
            "scene_checks": {
                scene.spec.scene_id: "passed" if scene.report.passed else "degraded"
                for scene in scenes
            },
        }
        reviewer = self._planner()
        if getattr(reviewer, "name", "") == "mock" or not hasattr(reviewer, "generate_structured"):
            return {**summary, "coherent": True, "natural_ending": True, "problems": []}
        # The full visual plan: a truncated one reads as a plan "cut off mid-sentence".
        outline = "\n".join(
            f"{index}. {scene.spec.scene_id} '{scene.spec.title}' "
            f"({scene.audio.duration_seconds:.1f}s). Objective: {scene.spec.learning_objective}\n"
            f"   Visual plan: {'; '.join(beat.visual for beat in scene.spec.visual_beats)}\n"
            f"   Narration: {scene.spec.narration}"
            for index, scene in enumerate(scenes, 1)
        )
        policy = SOURCE_POLICIES.get(getattr(self, "_source_scope", "none"), "")
        try:
            with (
                usage_stage("lesson_review"),
                call_timeout(getattr(self, "long_call_timeout_seconds", 420)),
            ):
                review = await reviewer.generate_structured(
                    "You review a finished narrated STEM lesson video before a student sees it. "
                    "Read the scenes in order. Report only real problems: a scene that does not "
                    "follow from the previous one, repeated content, a visual plan that does not "
                    "match its narration, symbols or numbers that change between scenes, an "
                    "ending that stops mid-thought, teases more content, or never resolves the "
                    "explanation, narration that talks about the teacher, a narrator or the "
                    "video in the third person instead of speaking as the teacher, or "
                    "narration that talks about the sources or how the lesson was made instead "
                    "of teaching (whether a source could be verified or found, what was "
                    "searched, notes about how the video was produced). A closing that speaks "
                    "to the learner, such as a recap, thanks, inviting questions or asking "
                    "whether it answered their question, is natural teaching, not a problem.\n"
                    "Return coherent, natural_ending, and issues. For each issue give scene_id "
                    "(the scene it is in; empty if it concerns the whole lesson) and the "
                    "problem. Set blocking=true only when that scene's narration is wrong for "
                    "the lesson (wrong facts or numbers, off-topic, a broken ending, talk about "
                    "sources or production, or content the source policy does not allow) AND "
                    "rewriting that one scene's narration fixes it; then write fix: a precise "
                    "instruction for rewriting it. Minor style notes are never blocking.\n"
                    + (f"{policy}\n" if policy else "")
                    + "\n"
                    + outline,
                    LessonFlowReview,
                )
        except Exception as error:  # noqa: BLE001 - review must never discard a built video
            logger.warning("Lesson flow review unavailable: %s", error)
            return {**summary, "coherent": None, "natural_ending": None, "problems": []}
        result = review.model_dump()
        result["problems"] = [
            f"{issue.scene_id}: {issue.problem}" if issue.scene_id else issue.problem
            for issue in review.issues
        ]
        return {**summary, **result}

    async def _repair_scenes(
        self,
        project_id: str,
        scenes: list[ProducedScene],
        fixes: dict[str, str],
        events: list[str],
        *,
        known_source_ids: set[str],
        code_skill_context: str,
    ) -> tuple[list[ProducedScene], list[str]]:
        """Rebuild the scenes the whole-lesson review found wrong, once each.

        The scene's narration is rewritten to the review's fix, then the scene
        is produced again (speech, animation, verification). A rebuild that
        fails keeps the original scene, so a repair never loses a lesson.
        """

        async def repair(scene: ProducedScene) -> ProducedScene:
            fix = fixes[scene.spec.scene_id]
            await self._notify_progress(
                "scene.review_repair", scene.spec.scene_id, {"reason": fix[:400]}
            )
            narration, pauses = speakable_with_pauses(
                await revise_narration(
                    self.llm,
                    with_markers(scene.spec.narration, scene.spec.pauses),
                    "A reviewer of the whole lesson found a problem in this part. " + fix,
                    learning_objective=scene.spec.learning_objective or "",
                    is_closing=scene is scenes[-1],
                    teaching_notes=getattr(self, "_teaching_notes", ""),
                )
            )
            if narration.strip() == scene.spec.narration.strip() and pauses == scene.spec.pauses:
                return scene
            spec = scene.spec.model_copy(
                update={
                    "version": scene.spec.version + 1,
                    "narration": narration,
                    "pauses": pauses,
                }
            )
            try:
                return await self.produce_scene(
                    project_id,
                    spec,
                    events,
                    artifact_version=max(
                        scene.render_version, scene.code_version, scene.audio_version
                    )
                    + 1,
                    known_source_ids=known_source_ids,
                    code_skill_context=code_skill_context,
                )
            except Exception as error:  # noqa: BLE001 - keep the original on a failed rebuild
                logger.warning("Review repair of %s failed: %s", scene.spec.scene_id, error)
                return scene

        targets = [scene for scene in scenes if scene.spec.scene_id in fixes]
        rebuilt = dict(
            zip(
                [scene.spec.scene_id for scene in targets],
                await asyncio.gather(*(repair(scene) for scene in targets)),
                strict=True,
            )
        )
        original = {scene.spec.scene_id: scene for scene in targets}
        repaired = [key for key, scene in rebuilt.items() if scene is not original[key]]
        return [rebuilt.get(scene.spec.scene_id, scene) for scene in scenes], repaired

    async def _localized_presentation(self, spec: SceneSpec) -> tuple[SceneSpec, str]:
        if self.translator is None:
            return spec, spec.narration
        key = (
            spec.scene_id,
            spec.version,
            spec.narration,
            self.text_language,
            self.voice_language,
        )
        cache = self.__dict__.setdefault("_presentation_cache", {})
        if key not in cache:
            events = getattr(self.translator, "events", None)
            seen = len(events) if isinstance(events, list) else 0
            cache[key] = await self.translator.localize_scene(
                spec, text_language=self.text_language, voice_language=self.voice_language
            )
            if isinstance(events, list) and len(events) > seen:
                # Translation checks that needed a retry or fell back to English.
                await self._notify_progress(
                    "translation.checked", spec.scene_id, {"findings": events[seen:]}
                )
        return cache[key]

    async def _synthesize_lesson_narration(self, project_id: str, specs: list[SceneSpec]) -> None:
        """Speak the lesson in as few TTS takes as possible, then cut them per scene.

        One continuous performance keeps the narrator's voice, pace and energy
        identical across scenes and leaves natural pauses at scene changes. A
        long lecture is spoken as several takes, split at chapter boundaries,
        each within the provider's input limit. A take whose cut positions are
        in doubt leaves its scenes to per-scene synthesis with the same voice.
        """
        self._lesson_audio = {}
        if getattr(self, "resume", False):
            for spec in specs:
                saved = self._read_json(
                    f"{project_id}/scenes/{spec.scene_id}/audio/v{spec.version}.json"
                )
                if saved and Path(saved.get("audio_path", "")).exists():
                    self._lesson_audio[spec.scene_id] = TTSResult.model_validate(saved)
            if self._lesson_audio:
                await self._notify_progress(
                    "narration.resumed", detail={"scenes": len(self._lesson_audio)}
                )
            specs = [spec for spec in specs if spec.scene_id not in self._lesson_audio]
        if len(specs) < 2 or getattr(self.tts, "name", "") == "mock":
            return
        spoken = [to_speakable((await self._localized_presentation(spec))[1]) for spec in specs]
        takes = plan_narration_takes(
            [spec.chapter_id for spec in specs], spoken, LESSON_NARRATION_MAX_CHARS
        )
        await self._notify_progress(
            "narration.started", detail={"scenes": len(specs), "takes": len(takes)}
        )
        limiter = asyncio.Semaphore(3)

        async def record(take_index: int, members: list[int]):
            async with limiter:
                return await self._record_take(project_id, take_index, members, specs, spoken)

        results = await asyncio.gather(
            *(record(index, members) for index, members in enumerate(takes, 1))
        )
        recorded = [result for result in results if result is not None]
        if len(recorded) < len(takes):
            await self._notify_progress(
                "narration.per_scene",
                detail={
                    "reason": "Could not find clean pauses between some scenes; recording those "
                    "scenes separately with the same voice."
                },
            )
        if not recorded:
            return
        await self._notify_progress(
            "narration.complete",
            detail={
                "seconds": round(sum(seconds for seconds, _ in recorded), 1),
                "voice": recorded[0][1],
                "single_take": len(takes) == 1 and len(recorded) == 1,
                "takes": len(recorded),
            },
        )

    async def _record_take(
        self,
        project_id: str,
        take_index: int,
        members: list[int],
        specs: list[SceneSpec],
        spoken: list[str],
    ) -> tuple[float, str | None] | None:
        """Speak one take and store each member scene's audio; None on failure."""
        texts = [respell(spoken[index], specs[index].pronunciations) for index in members]
        paths = [
            self.storage.local_path(
                f"{project_id}/scenes/{specs[index].scene_id}/audio/v{specs[index].version}.wav"
            )
            for index in members
        ]
        video = getattr(self, "_video_version", 1)
        name = (
            f"lesson-v{video}.wav"
            if take_index == 1
            else f"lesson-v{video}-take{take_index:02d}.wav"
        )
        take_path = self.storage.local_path(f"{project_id}/audio/{name}")
        try:
            take = await self.tts.synthesize(
                "\n\n".join(texts), take_path, language=self.voice_language
            )
            counts = [len(text.split()) for text in texts]
            # Measured word positions place each scene cut in the real pause
            # between scenes; the word-count guess can land a sentence early.
            words = await self._align(take.audio_path, "\n\n".join(texts))
            cuts = scene_cuts(words, counts) if words else None
            if cuts is None:
                words = None
            if len(members) == 1:
                durations = [take.duration_seconds]
                paths = [Path(take.audio_path)]
            else:
                durations = split_lesson_wav(Path(take.audio_path), counts, paths, cuts=cuts)
        except Exception as error:  # noqa: BLE001 - provider failures fall back per scene
            logger.warning("Narration take %d failed: %s", take_index, error)
            return None
        if durations is None:
            return None
        offset, first = 0.0, 0
        for index, text, path, duration, count in zip(
            members, texts, paths, durations, counts, strict=True
        ):
            update = {
                "audio_path": str(path),
                "duration_seconds": duration,
                "sentence_timings": estimate_sentence_timings(text, duration),
                "word_timings": [],
            }
            if words:
                scene_words = words_between(words[first : first + count], offset, duration)
                update["word_timings"] = scene_words
                update["sentence_timings"] = sentence_timings_from_words(
                    text, scene_words, duration
                )
            self._lesson_audio[specs[index].scene_id] = restore_spelling(
                take.model_copy(update=update), specs[index].pronunciations
            )
            offset += duration
            first += count
        return sum(durations), take.voice_id

    async def _align(self, audio_path: str, text: str) -> list[TimingCue] | None:
        """Measured word timings for a recording, or None to keep estimates."""
        aligner = getattr(self, "aligner", None)
        if aligner is None:
            return None
        return await asyncio.to_thread(aligner, audio_path, text)

    async def _measure_timings(self, audio: TTSResult, text: str) -> TTSResult:
        """Replace estimated cues on a freshly recorded scene with measured ones."""
        if audio.word_timings:
            return audio
        words = await self._align(audio.audio_path, text)
        if not words or len(words) != len(text.split()):
            return audio
        return audio.model_copy(
            update={
                "word_timings": words,
                "sentence_timings": sentence_timings_from_words(
                    text, words, audio.duration_seconds
                ),
            }
        )
