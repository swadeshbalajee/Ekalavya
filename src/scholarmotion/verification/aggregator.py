"""Combines verifier issues into a weighted score and a pass/fail report."""

from __future__ import annotations

from scholarmotion.schemas import VerificationIssue, VerificationReport

WEIGHTS = {"low": 0.05, "medium": 0.15, "high": 0.3, "critical": 0.7}


def aggregate_reports(
    scene_id: str, layers: dict[str, list[VerificationIssue]]
) -> VerificationReport:
    issues = [issue for values in layers.values() for issue in values]
    score = max(0.0, 1.0 - sum(WEIGHTS[issue.severity] for issue in issues))
    passed = score >= 0.8 and not any(issue.severity in {"high", "critical"} for issue in issues)
    return VerificationReport(
        scene_id=scene_id,
        passed=passed,
        score=score,
        issues=issues,
        layers={name: not values for name, values in layers.items()},
    )
