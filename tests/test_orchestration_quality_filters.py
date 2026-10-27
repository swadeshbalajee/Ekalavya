from scholarmotion.schemas import VerificationIssue
from scholarmotion.services.orchestration import _drop_stale_prompt_title_findings


def _issue(category: str, description: str) -> VerificationIssue:
    return VerificationIssue(
        scene_id="S01",
        category=category,
        severity="high",
        confidence=1.0,
        description=description,
        suggested_repair="Repair it.",
    )


def test_stale_prompt_title_finding_is_removed_when_current_source_has_topic_title():
    layers = {
        "vision": [
            _issue(
                "vision.other",
                "The title displays the prompt instruction 'Create a visual lesson'.",
            ),
            _issue("vision.overlap", "The gravity label overlaps the lean label."),
        ]
    }

    result = _drop_stale_prompt_title_findings(layers, "title = SafeTitle('Bicycle balance')")

    assert [issue.category for issue in result["vision"]] == ["vision.overlap"]


def test_real_prompt_title_finding_remains_blocking():
    layers = {
        "video": [
            _issue(
                "video.other",
                "The title displays the prompt instruction 'Create a visual lesson'.",
            )
        ]
    }

    result = _drop_stale_prompt_title_findings(
        layers, "title = SafeTitle('Create a visual lesson')"
    )

    assert len(result["video"]) == 1
