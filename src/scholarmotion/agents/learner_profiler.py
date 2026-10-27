"""The learner profile a build plans against.

Topic, level, known concepts, request type and requested length come from the
planner model's reading of the learner's words (``LessonPreparation``). Without
a planner decision the profile uses the lesson brief as the topic and plain
defaults; nothing is extracted from the text by patterns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scholarmotion.schemas import LearnerProfile, RequestType

if TYPE_CHECKING:
    from scholarmotion.agents.lesson_preparation import LessonPreparation

DEFAULT_VIDEO_DURATION_MINUTES = 3.0
MAX_VIDEO_DURATION_MINUTES = 30.0


def profile_request(
    request: str,
    *,
    target_duration_minutes: float | None = None,
    language: str = "English",
    has_paper: bool = False,
    preparation: LessonPreparation | None = None,
) -> LearnerProfile:
    planned = preparation is not None and preparation.planned
    duration = (
        (preparation.duration_minutes if planned else None)
        or target_duration_minutes
        or DEFAULT_VIDEO_DURATION_MINUTES
    )
    if planned:
        request_type = RequestType(preparation.request_type)
    else:
        request_type = (
            RequestType.PAPER_EXPLANATION if has_paper else RequestType.CONCEPT_EXPLANATION
        )
    return LearnerProfile(
        topic=(preparation.topic.strip() if planned else "") or request.strip(),
        target_level=(preparation.learner_level.strip() if planned else "") or "general",
        known_concepts=list(preparation.known_concepts) if planned else [],
        unknown_concepts=list(preparation.unknown_concepts) if planned else [],
        desired_duration_minutes=min(duration, MAX_VIDEO_DURATION_MINUTES),
        language=language,
        request_type=request_type,
    )
