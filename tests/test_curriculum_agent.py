from scholarmotion.agents.curriculum_agent import build_curriculum
from scholarmotion.agents.learner_profiler import profile_request
from scholarmotion.schemas import LearnerProfile


def test_curriculum_chapter_title_and_objective_fit_persistence_limits():
    topic = (
        "Create a beginner-friendly explanation of self-attention using the tokens "
        "'the river bank', detailing Q, K, and V rows, query-key dot product scores, "
        "softmax and weighted values, grounded in the imported paper"
    )
    plan = build_curriculum(LearnerProfile(topic=topic, desired_duration_minutes=3), [])

    assert plan.topic == topic
    # The planner writes the topic; a long one is only trimmed to fit storage.
    assert len(plan.items[-1].title) <= 160
    assert len(plan.items[-1].objective) <= 300


def test_the_planners_duration_beats_the_saved_default_and_is_capped_at_thirty_minutes():
    from scholarmotion.agents.lesson_preparation import LessonPreparation

    profile = profile_request(
        "Conservation of momentum.",
        target_duration_minutes=5,
        preparation=LessonPreparation(duration_minutes=12),
    )
    assert profile.desired_duration_minutes == 12
    capped = profile_request("Attention.", preparation=LessonPreparation(duration_minutes=45))
    assert capped.desired_duration_minutes == 30


def test_curriculum_keeps_a_short_requested_lesson_within_its_budget():
    profile = LearnerProfile(
        topic="Ohm's law",
        desired_duration_minutes=1,
        unknown_concepts=["electric current"],
    )

    plan = build_curriculum(profile, [])

    assert sum(item.duration_seconds for item in plan.items) == 60
    assert all(item.duration_seconds >= 8 for item in plan.items)
