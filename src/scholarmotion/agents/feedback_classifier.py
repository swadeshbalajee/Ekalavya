"""Classify a learner's edit request with the planner model.

The classification decides which artifacts an edit invalidates (script, scene
code or narration audio) and whether it is evidence of a recurring defect worth
remembering. It is a model decision; without the model an edit is treated as a
content change, which regenerates the most and so never under-repairs.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from scholarmotion.schemas import FeedbackClassification, FeedbackType

logger = logging.getLogger(__name__)

# Defects worth remembering so later scenes avoid them; preferences and content changes are not.
_RECURRING_DEFECTS = {
    FeedbackType.VISUAL_DEFECT,
    FeedbackType.MATH_DEFECT,
    FeedbackType.SEMANTIC_DEFECT,
    FeedbackType.AUDIO_DEFECT,
    FeedbackType.TIMING_DEFECT,
}


class _FeedbackKinds(BaseModel):
    kinds: list[FeedbackType] = Field(default_factory=list)
    category: str = ""


def _content_edit(instruction: str) -> list[FeedbackClassification]:
    return [
        FeedbackClassification(
            feedback_type=FeedbackType.CONTENT_EDIT,
            category="content.edit",
            instruction=instruction,
            memory_eligible=False,
        )
    ]


async def classify_feedback(llm, instruction: str) -> list[FeedbackClassification]:
    """What kind of change the learner asked for, most important first."""
    if llm is None or getattr(llm, "name", "") == "mock":
        return _content_edit(instruction)
    prompt = (
        "A learner asked for a change to part of an educational video. Classify the request.\n"
        "kinds (most important first, one or more):\n"
        "- CONTENT_EDIT: explain differently, another example, simpler, more detail\n"
        "- VISUAL_DEFECT: something on screen overlaps, is cut off, unreadable or misplaced\n"
        "- MATH_DEFECT: an equation, sign, variable or calculation is wrong\n"
        "- SEMANTIC_DEFECT: a statement is factually wrong or misleading\n"
        "- AUDIO_DEFECT: the narration voice, pronunciation or audio quality\n"
        "- TIMING_DEFECT: pacing, too fast or slow, visuals out of sync with speech\n"
        "- STYLE_PREFERENCE: colours, fonts, look and feel\n"
        "- SCOPE_CHANGE: change the topic or the whole lesson\n"
        "category: a short dotted label for the main issue, e.g. layout.overlap, "
        "math.wrong_sign, timing.too_fast, content.simpler.\n\n"
        f"REQUEST:\n{instruction}"
    )
    try:
        decision = await llm.generate_structured(prompt, _FeedbackKinds)
    except Exception as error:  # noqa: BLE001 - classification must never block an edit
        logger.warning("Feedback classification unavailable: %s", error)
        return _content_edit(instruction)
    kinds = list(dict.fromkeys(decision.kinds)) or [FeedbackType.CONTENT_EDIT]
    category = decision.category.strip() or "content.edit"
    return [
        FeedbackClassification(
            feedback_type=kind,
            category=category if index == 0 else kind.value.lower(),
            instruction=instruction,
            memory_eligible=kind in _RECURRING_DEFECTS,
        )
        for index, kind in enumerate(kinds)
    ]
