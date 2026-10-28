"""Name a chat from the learner's first request."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage

logger = logging.getLogger(__name__)


class ChatTitle(BaseModel):
    title: str = Field(description="A short, specific title for the chat")


async def title_for_request(llm, request: str) -> str | None:
    """A title in the language the learner wrote in; None when the model is unavailable."""
    if llm is None or getattr(llm, "name", "") == "mock":
        return None
    prompt = (
        "Write a short, specific title for a learning chat that starts with the message "
        "below, the way a good notebook heading names its topic. Use the language the "
        "learner wrote in. No quotation marks, no ending punctuation.\n\n"
        f"MESSAGE:\n{request[:4000]}"
    )
    try:
        with usage_stage("chat_title"):
            result = await llm.generate_structured(prompt, ChatTitle)
    except Exception as error:  # noqa: BLE001 - the chat keeps its provisional title
        logger.warning("Chat title unavailable: %s", error)
        return None
    title = " ".join(result.title.split()).strip("\"'“”‘’ ").rstrip(".")
    return title[:300] or None
