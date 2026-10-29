"""Let a writing agent pull detail the lesson plan left out.

The curator lists references: one line on what some detail is, with the passages
that hold it. Before writing, the script writer and the storyboard planner may ask
for any of them by number, or search the collected material in their own words. The
agent decides whether it needs anything; the learner's setting caps how many lookups
it gets. A reference returns its passages directly; a search runs over everything
collected for the lesson.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage
from scholarmotion.schemas import SourceReference

logger = logging.getLogger(__name__)

Search = Callable[[str], Awaitable[list[dict]]]


class LookupRequest(BaseModel):
    references: list[str] = Field(default_factory=list)
    searches: list[str] = Field(default_factory=list)


def references_text(references: list[SourceReference]) -> str:
    return "\n".join(f"R{index}: {item.about}" for index, item in enumerate(references, 1))


def _passage_text(chunk: dict) -> str:
    title = chunk.get("document_title") or "source"
    return f"({title}) {' '.join(str(chunk.get('text', '')).split())}"


async def look_up(
    llm,
    *,
    task: str,
    plan: str,
    references: list[SourceReference],
    chunks_by_id: dict[str, dict],
    search: Search | None,
    max_lookups: int,
    stage: str,
) -> str:
    """Passages the agent asked for, formatted for its prompt; empty when it needs none."""
    if (
        max_lookups <= 0
        or not (references or search)
        or getattr(llm, "name", "") == "mock"
        or not hasattr(llm, "generate_structured")
    ):
        return ""
    prompt = (
        f"You are about to {task}. You will work from the lesson plan below. Detail the plan "
        "left out is kept in the collected material: the references list what it is. If the "
        "plan is enough, ask for nothing. Otherwise ask for the references you need by "
        "number (like R2) and/or write short searches for anything else you need from the "
        f"material; at most {max_lookups} in total.\n\n"
        f"REFERENCES:\n{references_text(references) or '(none)'}\n\nLESSON PLAN:\n{plan}"
    )
    try:
        with usage_stage(stage):
            wanted = await llm.generate_structured(prompt, LookupRequest)
    except Exception as error:  # noqa: BLE001 - the plan alone is still enough to write
        logger.warning("Reference lookup unavailable: %r", error)
        return ""
    found: list[str] = []
    used = 0
    for label in wanted.references:
        if used >= max_lookups:
            break
        number = label.strip().upper().removeprefix("R")
        if not number.isdigit() or not 1 <= int(number) <= len(references):
            continue
        used += 1
        reference = references[int(number) - 1]
        passages = [chunks_by_id[key] for key in reference.source_ids if key in chunks_by_id]
        if passages:
            found.append(
                f"R{number} {reference.about}:\n"
                + "\n".join(_passage_text(chunk) for chunk in passages)
            )
    for query in wanted.searches:
        if used >= max_lookups or search is None or not query.strip():
            break
        used += 1
        passages = await search(query.strip())
        if passages:
            found.append(
                f"Search '{query.strip()}':\n"
                + "\n".join(_passage_text(chunk) for chunk in passages)
            )
    return "\n\n".join(found)
