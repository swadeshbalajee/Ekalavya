"""Write practice questions from a lesson, and grade a learner's answers.

The writer reads the video's narration scene by scene (or just a topic) and
decides how many questions, of which kind, test the ideas that matter. Each
question points at the scene that teaches it, so a wrong answer can send the
learner back to that moment. Free-text answers are graded by the model, which
explains what was missing rather than matching keywords.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage

logger = logging.getLogger(__name__)


class QuizQuestion(BaseModel):
    kind: Literal["choice", "short"]
    prompt: str
    options: list[str] = Field(default_factory=list, description="For choice questions")
    answer_index: int | None = Field(default=None, description="Correct option, 0-based")
    answer: str = Field(default="", description="A model answer, for short questions")
    explanation: str = ""
    scene_key: str | None = Field(default=None, description="The scene that teaches this")


class QuizDraft(BaseModel):
    title: str
    questions: list[QuizQuestion] = Field(min_length=1, max_length=15)


class GradedAnswer(BaseModel):
    index: int
    correct: bool
    feedback: str = ""


class GradedAnswers(BaseModel):
    results: list[GradedAnswer] = Field(default_factory=list)


class QuizUnavailable(RuntimeError):
    pass


def _usable(question: QuizQuestion) -> bool:
    if question.kind == "choice":
        return (
            len(question.options) >= 2
            and question.answer_index is not None
            and 0 <= question.answer_index < len(question.options)
        )
    return bool(question.prompt.strip())


async def write_quiz(
    llm,
    *,
    title: str,
    topic: str,
    scenes: list[dict],
    language: str,
    instruction: str = "",
) -> QuizDraft:
    """Questions on what the lesson teaches; ``scenes`` carry key, title and narration."""
    if llm is None or getattr(llm, "name", "") == "mock":
        raise QuizUnavailable("the quiz model is not configured")
    lesson = "\n\n".join(
        f"[{scene['key']}] {scene['title']}\n{scene.get('narration', '')}" for scene in scenes
    )
    prompt = (
        "You are a teacher writing a short practice quiz for a learner.\n"
        f"Write every question, option and explanation in {language}.\n"
        "Decide how many questions the material deserves and mix multiple-choice (kind "
        "'choice', with options and answer_index) and short written answers (kind 'short', "
        "with a model answer) as suits it. Test understanding, not wording. For each "
        "question give scene_key: the key of the scene that teaches it, when there is one.\n\n"
        f"LESSON: {title}\nTOPIC: {topic}\n"
    )
    if lesson:
        prompt += f"\nSCENES:\n{lesson[:24000]}\n"
    if instruction:
        prompt += f"\nTHE LEARNER ASKS: {instruction}\n"
    try:
        with usage_stage("quiz"):
            draft = await llm.generate_structured(prompt, QuizDraft)
    except Exception as error:
        logger.warning("Quiz writer unavailable: %s", error)
        raise QuizUnavailable(str(error)) from error
    known = {scene["key"] for scene in scenes}
    draft.questions = [question for question in draft.questions if _usable(question)]
    for question in draft.questions:
        if question.scene_key not in known:
            question.scene_key = None
    if not draft.questions:
        raise QuizUnavailable("the quiz model returned no usable questions")
    return draft


async def grade_written_answers(
    llm, *, questions: list[dict], answers: dict[int, str], language: str
) -> dict[int, GradedAnswer]:
    """The model's judgement of each written answer, with feedback in ``language``."""
    if not answers:
        return {}
    items = "\n\n".join(
        f"#{index}\nQUESTION: {questions[index]['prompt']}\n"
        f"MODEL ANSWER: {questions[index].get('answer', '')}\n"
        f"LEARNER'S ANSWER: {answer}"
        for index, answer in answers.items()
    )
    prompt = (
        "Grade a learner's written answers. An answer is correct when it shows the "
        "understanding the question asks for, even in different words. For each, give "
        f"short, kind feedback in {language} on what was right or missing.\n\n{items}"
    )
    try:
        with usage_stage("quiz_grading"):
            graded = await llm.generate_structured(prompt, GradedAnswers)
    except Exception as error:
        logger.warning("Quiz grading unavailable: %s", error)
        raise QuizUnavailable(str(error)) from error
    return {item.index: item for item in graded.results if item.index in answers}
