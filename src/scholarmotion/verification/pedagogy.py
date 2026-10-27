"""Checks scenes against their teaching objectives and storyboard."""

from __future__ import annotations

import ast
import re

from scholarmotion.schemas import SceneSpec, VerificationIssue


def verify_pedagogy(spec: SceneSpec) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    if not spec.learning_objective.strip():
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.missing_objective",
                severity="high",
                confidence=1,
                description="Scene has no learning objective.",
                suggested_repair="Revise curriculum and SceneSpec.",
                correction_memory_candidate=False,
            )
        )
    if len(spec.visual_beats) > max(8, spec.duration_target_seconds / 4):
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.cognitive_load",
                severity="medium",
                confidence=0.9,
                description="Too many visual beats for the target duration.",
                suggested_repair="Split or simplify the scene.",
                correction_memory_candidate=False,
            )
        )
    if not spec.narration.strip():
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.missing_narration",
                severity="critical",
                confidence=1,
                description="Narration is empty.",
                suggested_repair="Regenerate the narration block.",
                correction_memory_candidate=False,
            )
        )
    return issues


_GENERIC_STORYBOARD_BEATS = (
    "Introduce one labeled visual model.",
    "Animate the relevant transformation and highlight what stays invariant.",
    "Recap with a compact equation or label, keeping subtitles clear.",
)


def verify_rendered_storyboard(
    spec: SceneSpec, python_code: str, *, allow_placeholder: bool = False
) -> list[VerificationIssue]:
    """Reject stage directions and spoken narration copied into Manim text."""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return []
    rendered_text = []
    constructors = {"Text", "SafeTitle", "TimelineLabel", "MarkupText"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name not in constructors:
            continue
        rendered_text.extend(
            item.value
            for item in node.args[:1]
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
        rendered_text.extend(
            item.value
            for item in node.keywords
            if item.arg in {"text", "string"}
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        )
    normalized_code = re.sub(r"\s+", " ", " ".join(rendered_text)).casefold()
    issues = []
    if "SCHOLARMOTION_GENERIC_PLACEHOLDER = True" in python_code and not allow_placeholder:
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.unrelated_placeholder",
                severity="high",
                confidence=1,
                description="Generic input/output artwork cannot demonstrate this scene's narration.",
                suggested_repair="Generate a domain-specific animation from the exact narration and storyboard; do not substitute a generic template.",
            )
        )
    normalized_narration = re.sub(r"\s+", " ", spec.narration).casefold()
    spoken_overlap = next(
        (
            text
            for text in rendered_text
            # Short headings and conventional labels can legitimately use a
            # topic phrase also spoken in the lesson. Full caption-like spans
            # of eight or more words are forbidden.
            if len(text.split()) >= 8
            and re.sub(r"\s+", " ", text).casefold() in normalized_narration
        ),
        None,
    )
    if spoken_overlap:
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.spoken_text_rendered",
                severity="high",
                confidence=1,
                description="A phrase from the spoken narration is embedded in Manim text.",
                suggested_repair=(
                    "Remove narration text from the scene. Speech is delivered through audio and "
                    "subtitles; keep only short visual labels, equations, values, and source markers."
                ),
                correction_memory_candidate=False,
            )
        )
    for direction in _GENERIC_STORYBOARD_BEATS:
        if direction.casefold() not in normalized_code:
            continue
        issues.append(
            VerificationIssue(
                scene_id=spec.scene_id,
                category="pedagogy.stage_direction_rendered",
                severity="high",
                confidence=1,
                description="A storyboard instruction is displayed as learner-facing text.",
                suggested_repair=(
                    "Remove storyboard directions from Text objects. Replace them with a "
                    "topic-specific diagram, labeled quantities, and the concept's equation."
                ),
                correction_memory_candidate=False,
            )
        )
        break
    return issues
