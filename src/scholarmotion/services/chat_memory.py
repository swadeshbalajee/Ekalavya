"""What the agents remember of a chat: recent turns word for word, older ones summarised.

The last N learner turns (and the replies to them) are kept verbatim. Everything
older is folded into one running summary stored on the chat. Compaction runs in the
background after a reply, and only once the not-yet-summarised older turns pass a
size budget, so short chats never pay for it. Until then those turns are simply
passed on verbatim, so nothing is ever dropped. The original messages stay in the
database untouched; an edit to a summarised message clears the summary so it is
rebuilt from the branch that is now active.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel

from scholarmotion.observability.usage import usage_stage
from scholarmotion.persistence.models import Project, TutorMessage, update_quietly

logger = logging.getLogger(__name__)

# Older turns are summarised only once they add up to this many characters.
COMPACT_AFTER_CHARS = 3000

SUMMARY_PROMPT = """You keep the memory of a tutoring chat for the assistant who continues it.
Rewrite the summary so it also covers the older turns below. Keep, in plain English:
- standing preferences and instructions the learner gave (languages, voice and style of the
  teacher, use of their sources, length, level), with the latest version of each;
- what was asked and answered, and the decisions made;
- every video made in the chat: its topic and what was changed in it;
- what the learner found confusing, and questions still open.
Drop greetings and repetition. Do not invent anything. At most about 400 words."""


class ChatSummary(BaseModel):
    summary: str


def _split(messages: list[TutorMessage], keep_turns: int) -> tuple[list, list]:
    """(older, recent): recent starts at the learner's keep_turns-th last message."""
    starts = [index for index, message in enumerate(messages) if message.role == "user"]
    if len(starts) <= keep_turns:
        return [], messages
    cut = starts[-keep_turns]
    return messages[:cut], messages[cut:]


def _line(message: TutorMessage) -> str:
    return f"{message.role.upper()}: {message.canonical_text}"


def _unsummarised(project: Project, older: list[TutorMessage]) -> list[TutorMessage]:
    until: datetime | None = project.chat_summary_until if project.chat_summary else None
    if until is None:
        return older
    return [message for message in older if _aware(message.created_at) > _aware(until)]


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; PostgreSQL aware ones.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def recall(
    project: Project, messages: list[TutorMessage], keep_turns: int
) -> tuple[str, list[TutorMessage]]:
    """(earlier, recent): the summary plus not-yet-summarised older turns, and the
    recent messages to show word for word."""
    older, recent = _split(messages, keep_turns)
    parts = []
    if project.chat_summary and older:
        parts.append(f"SUMMARY OF EARLIER TURNS:\n{project.chat_summary}")
    pending = _unsummarised(project, older) if older else []
    if pending:
        parts.append("\n".join(_line(message) for message in pending))
    return "\n".join(parts), recent


def conversation_context(
    project: Project, messages: list[TutorMessage], keep_turns: int
) -> tuple[str, list[TutorMessage]]:
    """The conversation text for a prompt, and the recent messages it ends with."""
    earlier, recent = recall(project, messages, keep_turns)
    latest = "\n".join(_line(message) for message in recent)
    return (f"{earlier}\nRECENT TURNS:\n{latest}" if earlier else latest), recent


async def compact(session, llm, project: Project, messages: list[TutorMessage], keep_turns: int):
    """Fold older turns into the running summary once they pass the size budget."""
    older, _recent = _split(messages, keep_turns)
    pending = _unsummarised(project, older)
    if not pending or sum(len(message.canonical_text or "") for message in pending) < (
        COMPACT_AFTER_CHARS
    ):
        return False
    if getattr(llm, "name", "") == "mock" or not hasattr(llm, "generate_structured"):
        return False
    prompt = (
        f"{SUMMARY_PROMPT}\n\nCURRENT SUMMARY:\n{project.chat_summary or '(none yet)'}\n\n"
        "OLDER TURNS TO ADD:\n" + "\n".join(_line(message) for message in pending)
    )
    try:
        with usage_stage("chat_summary"):
            result = await llm.generate_structured(prompt, ChatSummary)
    except Exception as error:  # noqa: BLE001 - the turns stay verbatim until next time
        logger.warning("Chat compaction failed: %r", error)
        return False
    summary = result.summary.strip()
    if not summary:
        return False
    await update_quietly(
        session, project, chat_summary=summary, chat_summary_until=pending[-1].created_at
    )
    return True


def forget_from(project: Project, edited_at: datetime) -> bool:
    """An edit to a message the summary covers makes the summary stale."""
    if project.chat_summary_until is None or _aware(edited_at) > _aware(project.chat_summary_until):
        return False
    project.chat_summary = None
    project.chat_summary_until = None
    return True
