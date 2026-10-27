"""Turns learner feedback into reusable correction candidates."""

from __future__ import annotations

from scholarmotion.schemas import FeedbackClassification


def correction_candidate(
    classification: FeedbackClassification, *, scene_id: str, evidence_id: str
) -> dict | None:
    if not classification.memory_eligible:
        return None
    return {
        "category": classification.category,
        "trigger_conditions": classification.instruction,
        "recommended_fix": f"Prevent {classification.category} in scenes with matching primitives.",
        "evidence_count": 1,
        "confidence": 0.75,
        "status": "candidate",
        "evidence": [{"scene_id": scene_id, "evidence_id": evidence_id}],
    }
