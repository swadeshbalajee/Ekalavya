"""Checks that scenes cite only known source IDs."""

from __future__ import annotations

from scholarmotion.schemas import SceneSpec, VerificationIssue


def verify_source_grounding(spec: SceneSpec, known_source_ids: set[str]) -> list[VerificationIssue]:
    missing = sorted(set(spec.source_ids) - known_source_ids)
    if not missing:
        return []
    return [
        VerificationIssue(
            scene_id=spec.scene_id,
            category="semantic.missing_source",
            severity="high",
            confidence=1,
            objects=missing,
            description="Scene references unknown source IDs.",
            suggested_repair="Return to knowledge gathering and select valid ledger claims.",
            correction_memory_candidate=False,
        )
    ]
