"""Cover the policy that decides when an imperfect scene still ships.

When repairs run out, dropping the scene also deletes its narration and audio
from the lesson. A scene that rendered and only has placement faults is worth
keeping; one that crashed or teaches something wrong is not.
"""

from __future__ import annotations

import pytest

from scholarmotion.services.orchestration import _only_cosmetic, _reject_incomplete_lesson


class _Issue:
    def __init__(self, category: str, severity: str = "high"):
        self.category = category
        self.severity = severity


class _Report:
    def __init__(self, issues):
        self.issues = issues


def test_layout_only_issues_are_cosmetic():
    report = _Report([_Issue("layout.overlap"), _Issue("layout.subtitle_collision")])
    assert _only_cosmetic(report) is True


def test_low_severity_layout_is_cosmetic():
    assert _only_cosmetic(_Report([_Issue("layout.text_too_small", "medium")])) is True


@pytest.mark.parametrize(
    "category",
    ["render.execution", "math.identity", "semantic.grounding", "pedagogy.objective"],
)
def test_correctness_issues_are_never_cosmetic(category):
    """These change what the scene teaches, so they must still block."""
    assert _only_cosmetic(_Report([_Issue(category)])) is False


def test_one_correctness_issue_disqualifies_the_whole_scene():
    report = _Report([_Issue("layout.overlap"), _Issue("math.identity")])
    assert _only_cosmetic(report) is False


def test_critical_layout_issue_is_not_cosmetic():
    assert _only_cosmetic(_Report([_Issue("layout.overlap", "critical")])) is False


def test_missing_or_clean_report_is_not_a_fallback():
    """A passing scene takes the normal path; None means nothing was verified."""
    assert _only_cosmetic(None) is False
    assert _only_cosmetic(_Report([])) is False


def test_blocked_scene_prevents_publishing_a_partial_lesson():
    with pytest.raises(RuntimeError, match="incomplete.*S03.*No partial video"):
        _reject_incomplete_lesson([{"scene_id": "S03"}])


def test_complete_scene_set_can_be_published():
    _reject_incomplete_lesson([])
