"""Decides the repair action for a scene after verification fails."""

from __future__ import annotations

from enum import StrEnum

from scholarmotion.schemas import SceneStatus, VerificationIssue


class RepairAction(StrEnum):
    PATCH_CODE = "patch_code"
    REGENERATE_CODE = "regenerate_code"
    REVISE_SPEC = "revise_spec"
    SIMPLIFY = "simplify"
    BLOCK = "block"


ALLOWED_TRANSITIONS = {
    SceneStatus.PLANNED: {SceneStatus.SPEC_READY},
    SceneStatus.SPEC_READY: {
        SceneStatus.CODE_GENERATING,
        SceneStatus.AUDIO_GENERATING,
        SceneStatus.STALE,
    },
    SceneStatus.CODE_GENERATING: {SceneStatus.CODE_READY, SceneStatus.FAILED},
    SceneStatus.AUDIO_GENERATING: {SceneStatus.AUDIO_READY, SceneStatus.FAILED},
    SceneStatus.CODE_READY: {
        SceneStatus.AUDIO_GENERATING,
        SceneStatus.TIMING_RECONCILING,
        SceneStatus.DRAFT_RENDERING,
    },
    SceneStatus.AUDIO_READY: {SceneStatus.CODE_GENERATING, SceneStatus.TIMING_RECONCILING},
    SceneStatus.TIMING_RECONCILING: {SceneStatus.DRAFT_RENDERING, SceneStatus.REPAIRING},
    SceneStatus.DRAFT_RENDERING: {SceneStatus.VERIFYING, SceneStatus.REPAIRING, SceneStatus.FAILED},
    SceneStatus.VERIFYING: {
        SceneStatus.FINAL_RENDERING,
        SceneStatus.COMPLETE,
        SceneStatus.REPAIRING,
    },
    SceneStatus.REPAIRING: {
        SceneStatus.CODE_GENERATING,
        SceneStatus.SPEC_READY,
        SceneStatus.BLOCKED,
    },
    SceneStatus.FINAL_RENDERING: {SceneStatus.COMPLETE, SceneStatus.REPAIRING, SceneStatus.FAILED},
    SceneStatus.FAILED: {SceneStatus.REPAIRING, SceneStatus.BLOCKED},
    SceneStatus.STALE: {
        SceneStatus.SPEC_READY,
        SceneStatus.CODE_GENERATING,
        SceneStatus.AUDIO_GENERATING,
    },
    SceneStatus.COMPLETE: {SceneStatus.STALE},
    SceneStatus.BLOCKED: {SceneStatus.REPAIRING},
}


class Manager:
    # Attempts 0–3 exercise the model-authored draft and three repairs.  The
    # fourth repair action produces the deterministic simplified scene, which
    # must receive one final render/verification pass of its own (attempt 5).
    # Keeping this at four used to generate that fallback after the loop had
    # already exhausted its last iteration, so it could never be published.
    max_repair_attempts = 5

    def transition(self, current: SceneStatus, target: SceneStatus) -> SceneStatus:
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid scene transition: {current} -> {target}")
        return target

    def route_issue(self, issue: VerificationIssue) -> str:
        category = issue.category
        if category.startswith(("render.", "layout.")):
            return "code"
        if category.startswith("math."):
            return "knowledge_then_spec"
        if category.startswith("pedagogy."):
            return "curriculum_or_storyboard"
        if category.startswith("timing."):
            return "timing"
        if category.startswith("semantic."):
            return "script_or_storyboard"
        return "code"

    def repair_action(self, attempt: int) -> RepairAction:
        if attempt <= 0:
            raise ValueError("repair attempt numbers begin at 1")
        return {
            1: RepairAction.PATCH_CODE,
            2: RepairAction.REGENERATE_CODE,
            3: RepairAction.REVISE_SPEC,
            4: RepairAction.SIMPLIFY,
        }.get(attempt, RepairAction.BLOCK)
