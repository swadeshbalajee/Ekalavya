"""Turn a learner's goal into a day-by-day study plan.

The planner sees the goal, the time available, the videos the learner already
has (their own, saved and relevant public ones) and, when replanning, what they
have done so far. It decides what to study, in what order, and which tasks are
watching an existing video, practising, reading, reviewing, or making a new
video for a topic nobody has covered yet.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage

logger = logging.getLogger(__name__)

TaskKind = Literal["watch", "create", "practice", "read", "review"]


class PlannedTask(BaseModel):
    day: int = Field(ge=0, description="Days after the plan's start date (0 = start day)")
    kind: TaskKind
    title: str
    detail: str = ""
    minutes: int = Field(default=15, ge=1, le=600)
    video_id: str | None = Field(
        default=None, description="For watch/practice: the id of one of the AVAILABLE VIDEOS"
    )
    topic: str | None = Field(
        default=None,
        description="For create/practice without a video: the topic, as a request for a lesson",
    )


class StudyPlanDraft(BaseModel):
    title: str
    summary: str = ""
    tasks: list[PlannedTask] = Field(default_factory=list)


class StudyPlanUnavailable(RuntimeError):
    pass


def _video_lines(videos: list[dict]) -> str:
    if not videos:
        return "(none)"
    return "\n".join(
        f"- id={item['id']} | {item['title']}"
        + (f" | {item['subject']}" if item.get("subject") else "")
        + (f" | {round(item['duration'] / 60, 1)} min" if item.get("duration") else "")
        + (f" | {item['note']}" if item.get("note") else "")
        for item in videos
    )


async def plan_study(
    llm,
    *,
    goal: str,
    start: date,
    end: date | None,
    minutes_per_day: int,
    language: str,
    videos: list[dict],
    progress: str = "",
    instruction: str = "",
) -> StudyPlanDraft:
    """A plan whose watch tasks only point at videos the learner can open."""
    if llm is None or getattr(llm, "name", "") == "mock":
        raise StudyPlanUnavailable("the planning model is not configured")
    horizon = f"until {end.isoformat()}" if end else "over a span you judge right for the goal"
    prompt = (
        "You are a study coach. Make a realistic day-by-day study plan for this learner.\n"
        f"GOAL: {goal}\n"
        f"START DATE: {start.isoformat()} ({start.strftime('%A')}); plan {horizon}.\n"
        f"TIME AVAILABLE: about {minutes_per_day} minutes per day.\n"
        f"Write titles, details and the summary in {language}.\n\n"
        "Task kinds: watch (an existing video), practice (a quiz; give video_id when a video "
        "covers it, else topic), read (a source or textbook section, say which in detail), "
        "review (revisit notes or earlier material), create (a new explainer video for a "
        "topic no available video covers; topic is the lesson request).\n"
        "Only use video ids from AVAILABLE VIDEOS. Decide the pacing, order, rest days and "
        "revision yourself from the goal and the time available.\n\n"
        f"AVAILABLE VIDEOS:\n{_video_lines(videos)}\n"
    )
    if progress:
        prompt += f"\nPROGRESS SO FAR (replan the remaining days from this):\n{progress}\n"
    if instruction:
        prompt += f"\nTHE LEARNER ASKS: {instruction}\n"
    try:
        with usage_stage("study_plan"):
            draft = await llm.generate_structured(prompt, StudyPlanDraft)
    except Exception as error:
        logger.warning("Study planner unavailable: %s", error)
        raise StudyPlanUnavailable(str(error)) from error
    known = {item["id"] for item in videos}
    for task in draft.tasks:
        if task.video_id not in known:
            # The model named a video the learner cannot open; keep the task as a topic.
            if task.video_id and not task.topic:
                task.topic = task.title
            task.video_id = None
            if task.kind == "watch":
                task.kind = "create"
                task.topic = task.topic or task.title
    if end is not None:
        span = (end - start).days
        draft.tasks = [task for task in draft.tasks if task.day <= span]
    draft.tasks.sort(key=lambda task: task.day)
    return draft
