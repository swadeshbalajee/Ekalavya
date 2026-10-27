from __future__ import annotations

from scholarmotion.schemas import CurriculumItem, CurriculumPlan, KnowledgeCard, LearnerProfile


def _curriculum_title(topic: str) -> str:
    """The lesson topic as a title; its wording comes from the planner."""
    return " ".join(topic.split()).strip(" .,:;—-")[:160] or "Lesson"


def build_curriculum(
    profile: LearnerProfile, cards: list[KnowledgeCard], prerequisite_chain: list[str] | None = None
) -> CurriculumPlan:
    chain = [
        item
        for item in (prerequisite_chain or profile.unknown_concepts)
        if item.lower() not in {known.lower() for known in profile.known_concepts}
    ]
    topic_title = _curriculum_title(profile.topic)
    titles = [*chain, topic_title]
    titles = list(dict.fromkeys(item.strip() for item in titles if item.strip())) or [profile.topic]
    total = profile.desired_duration_minutes * 60
    weights = [0.75 if title != topic_title else 2.0 for title in titles]
    unit = total / sum(weights)
    source_ids = list(dict.fromkeys(source for card in cards for source in card.source_ids))
    items = [
        CurriculumItem(
            title=title,
            objective=(
                f"Build an intuitive model of {title}, explain its governing relationship, "
                "work a concrete example, and test one boundary or common misconception."
            ),
            # Preserve the learner's duration contract.  A fixed twenty-second
            # floor made a one-minute lesson with multiple prerequisites exceed
            # its requested running time before narration was even written.
            duration_seconds=max(8, unit * weight),
            prerequisites=titles[:index],
            source_ids=source_ids[:8],
        )
        for index, (title, weight) in enumerate(zip(titles, weights))
    ]
    return CurriculumPlan(
        topic=profile.topic, items=items, skipped_known_concepts=profile.known_concepts
    )
