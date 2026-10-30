"""The video reviewer's checklist and how a scene is scored against it.

An open "does this make sense?" review let real faults through: labels piled
on bars, highlight boxes on the wrong part of a figure, empty placeholder
boxes. The reviewer now answers every item below, and a scene passes when its
weighted score is at least 90% and every must-pass item passes.
"""

from __future__ import annotations

from dataclasses import dataclass

from scholarmotion.schemas import VerificationIssue

PASS_SCORE = 0.9


@dataclass(frozen=True)
class CheckItem:
    id: str
    weight: str  # must | high | medium | low
    question: str


CHECKLIST: tuple[CheckItem, ...] = (
    CheckItem(
        "text_overlap",
        "must",
        "No text, label or number overlaps other text, a shape, a bar or a line once the "
        "picture has settled. Anything fading in or out, moving or transforming does not count.",
    ),
    CheckItem(
        "in_frame",
        "must",
        "Nothing is cut off at the frame edges, and nothing sits in the bottom subtitle band.",
    ),
    CheckItem(
        "figures_shown",
        "must",
        "Every paper figure or table the narration refers to is on screen and readable while it "
        "is discussed (pass when the scene uses none).",
    ),
    CheckItem(
        "highlight_targets",
        "high",
        "Every highlight box, arrow or pointer surrounds or points at exactly the thing the "
        "narration names at that moment, not a legend, a neighbouring panel or empty space "
        "(pass when there are none).",
    ),
    CheckItem(
        "values_match",
        "high",
        "Numbers, labels, axis values and bar or point positions match what is said.",
    ),
    CheckItem(
        "equations_correct",
        "high",
        "Equations are complete, typeset cleanly (no boxes or jumbled glyphs) and match what is "
        "said (pass when there are none).",
    ),
    CheckItem(
        "audio_video_sync",
        "medium",
        "Each narration sentence has a matching visual change at about the time it is spoken.",
    ),
    CheckItem(
        "no_placeholders",
        "medium",
        "No empty boxes or shapes that stand for nothing, no stray marks, and nothing left over "
        "from an earlier step that no longer belongs.",
    ),
    CheckItem(
        "readable",
        "medium",
        "All text is large enough and has enough contrast to read comfortably.",
    ),
    CheckItem(
        "not_crowded",
        "low",
        "The screen is not crowded; groups have clear space between them.",
    ),
)

WEIGHTS = {"must": 3, "high": 3, "medium": 2, "low": 1}
_SEVERITY = {"must": "high", "high": "high", "medium": "medium", "low": "low"}


def edit_check(request: str) -> CheckItem:
    """A must-pass item for an edited scene: the learner's change is really there."""
    return CheckItem(
        "edit_done",
        "must",
        f"The learner asked for this change to the scene: {request.strip()[:400]!r}. It is "
        "visible or audible in this version.",
    )


def checklist_prompt(extra: tuple[CheckItem, ...] = ()) -> str:
    """The checklist and answer format for the video reviewer's prompt."""
    lines = "\n".join(f"- {item.id}: {item.question}" for item in (*CHECKLIST, *extra))
    return (
        "Answer every checklist item for the whole scene, in this order:\n"
        f"{lines}\n"
        "For a failed item, give when it is visible, what you see, and one concrete code change. "
        "Return compact JSON only: "
        '{"summary":"one or two sentences on what a student sees and hears",'
        '"checklist":[{"id":"text_overlap","pass":true,"timestamp_start_seconds":null,'
        '"timestamp_end_seconds":null,"observed":"","concrete_code_change":""}]}'
    )


@dataclass(frozen=True)
class ChecklistResult:
    passed: bool
    score: float
    failed: list[str]
    issues: list[VerificationIssue]


def score_checklist(
    scene_id: str, answers: list[dict], extra: tuple[CheckItem, ...] = ()
) -> ChecklistResult:
    """Score the reviewer's answers; an item it did not answer counts as passed.

    Failed items become repair findings. When the scene passes overall they are
    low severity (feedback only); when it fails they keep their weight's
    severity, and a summary finding says why the scene failed.
    """
    items = (*CHECKLIST, *extra)
    by_id = {answer["id"]: answer for answer in answers}
    total = sum(WEIGHTS[item.weight] for item in items)
    failed = [item for item in items if by_id.get(item.id, {}).get("passed") is False]
    score = 1 - sum(WEIGHTS[item.weight] for item in failed) / total
    must_failed = [item for item in failed if item.weight == "must"]
    passed = score >= PASS_SCORE and not must_failed
    issues = [
        VerificationIssue(
            scene_id=scene_id,
            category=f"video.checklist.{item.id}",
            severity="low" if passed else _SEVERITY[item.weight],
            confidence=0.8,
            description=(
                f"Check failed: {item.question}"
                + (
                    f" Seen: {by_id[item.id]['description']}"
                    if by_id[item.id]["description"]
                    else ""
                )
            ),
            suggested_repair=by_id[item.id]["suggested_repair"]
            or "Change the scene so this check passes.",
        )
        for item in failed
    ]
    if not passed and not any(issue.severity == "high" for issue in issues):
        issues.append(
            VerificationIssue(
                scene_id=scene_id,
                category="video.checklist.score",
                severity="high",
                confidence=0.8,
                description=(
                    f"The scene scored {score:.0%} on the review checklist; it needs "
                    f"{PASS_SCORE:.0%}. Failed: {', '.join(item.id for item in failed)}."
                ),
                suggested_repair="Fix the failed checks listed above.",
            )
        )
    return ChecklistResult(passed, score, [item.id for item in failed], issues)
