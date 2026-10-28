"""Detects render failures and raw LaTeX shown as plain text."""

from __future__ import annotations

from scholarmotion.manim_runtime.markers import CUE_OVERFLOW_MARKER
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


def verify_cue_overflow(scene_id: str, log_text: str, cue_count: int) -> list[VerificationIssue]:
    """Animations the code started after its last narration cue.

    CueClock skips them so nothing is cut off mid-fade at the scene boundary,
    but a skipped beat means the code had more visual beats than spoken cues.
    """
    marker = f"{CUE_OVERFLOW_MARKER} "
    counts = [
        int(line.split()[1])
        for line in log_text.splitlines()
        if line.startswith(marker) and line.split()[1].isdigit()
    ]
    if not counts:
        return []
    return [
        VerificationIssue(
            scene_id=scene_id,
            category="video.beat_after_narration",
            severity="high",
            confidence=1.0,
            description=(
                f"The code called clock.play {max(counts)} time(s) after its last cue: it has "
                f"more visual beats than the {cue_count} narration cue(s), so those visuals "
                "were skipped."
            ),
            suggested_repair=(
                f"Write exactly {cue_count} beats, one per cue: call clock.next_cue() after each "
                "beat except the last and clock.finish() after the last. Move any later visual "
                "into the cue whose sentence describes it."
            ),
        )
    ]
