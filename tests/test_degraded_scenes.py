"""Cover the policy that decides when an imperfect scene still ships.

When repairs run out, dropping the scene also deletes its narration and audio
from the lesson. A scene that rendered and only has placement faults is worth
keeping as-is; otherwise the plain safe scene, then the best rendered attempt,
then a still frame under the narration keep the lesson complete.
"""

from __future__ import annotations

import pytest

from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.schemas import VerificationIssue, VerificationReport
from scholarmotion.services.orchestration import (
    _attempt_rank,
    _only_cosmetic,
    _reject_empty_lesson,
)


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


def _report(score: float, *severities: str) -> VerificationReport:
    return VerificationReport(
        scene_id="S01",
        passed=False,
        score=score,
        issues=[
            VerificationIssue(
                scene_id="S01",
                category="video.mismatch",
                severity=severity,
                confidence=1,
                description="x",
                suggested_repair="y",
            )
            for severity in severities
        ],
    )


def test_best_attempt_has_fewest_serious_issues_then_highest_score():
    attempts = [
        _report(0.9, "critical"),
        _report(0.4, "high", "high"),
        _report(0.5, "high"),
        _report(0.7, "high"),
    ]
    assert min(attempts, key=_attempt_rank).score == 0.7


def test_a_partial_lesson_is_published_when_some_scenes_exist():
    _reject_empty_lesson(["S01"], [{"scene_id": "S03"}])


def test_only_an_empty_lesson_fails():
    with pytest.raises(RuntimeError, match="no scene could be produced.*S03"):
        _reject_empty_lesson([], [{"scene_id": "S03"}])


@pytest.mark.media
def test_still_scene_is_a_playable_clip_of_the_narration_length(tmp_path):
    assembler = FFmpegAssembler(mock_missing=False)
    path = tmp_path / "still.mp4"
    if not assembler.still_scene(path, 2.0):
        pytest.skip("ffmpeg unavailable")
    assert abs(assembler.probe_duration(str(path)) - 2.0) < 0.2
