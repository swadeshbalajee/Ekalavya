"""Detects render failures and raw LaTeX shown as plain text."""

from __future__ import annotations

from scholarmotion.manim_runtime.sandbox import RenderResult
from scholarmotion.manim_runtime.validator import (
    find_raw_tex_in_text,
    find_unescaped_latex_specials,
)
from scholarmotion.schemas import VerificationIssue


def verify_no_raw_latex_text(scene_id: str, code: str) -> list[VerificationIssue]:
    findings = find_raw_tex_in_text(code) + find_unescaped_latex_specials(code)
    return [
        VerificationIssue(
            scene_id=scene_id,
            category="render.raw_latex_text",
            severity="high",
            confidence=0.95,
            description=finding,
            suggested_repair=finding,
            correction_memory_candidate=True,
        )
        for finding in findings
    ]


def verify_render(scene_id: str, result: RenderResult) -> list[VerificationIssue]:
    if result.success and result.video_path:
        return []
    detail = result.error or "render failed"
    return [
        VerificationIssue(
            scene_id=scene_id,
            category="render.execution",
            severity="critical",
            confidence=1,
            description=detail,
            suggested_repair=(
                "The previous attempt failed with this exact error; fix the root cause, "
                f"do not repeat it:\n{detail}"
            ),
            correction_memory_candidate=False,
        )
    ]
