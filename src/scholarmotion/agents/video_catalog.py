"""Describe a finished lesson video for the public catalog.

The model reads what the video teaches (the request, scene titles and narration)
and writes the title, summary, subject and search tags a learner would look
for. Subjects are the model's own words, so the catalog's filters grow with the
videos people publish instead of following a fixed list.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage

logger = logging.getLogger(__name__)


class VideoCatalogEntry(BaseModel):
    title: str = Field(description="A short, specific title a learner would click")
    description: str = Field(description="Two or three sentences on what the video teaches")
    subject: str = Field(description="The broad subject, in one or two words")
    tags: list[str] = Field(default_factory=list, description="Search keywords and concepts")
    level: str = Field(default="", description="Who it suits, in a few words, e.g. Class 11")


def _fallback(title: str, request: str) -> VideoCatalogEntry:
    return VideoCatalogEntry(title=title, description=request[:400], subject="", tags=[])


async def describe_video(
    llm,
    *,
    title: str,
    request: str,
    scenes: list[str],
    narration: str,
    language: str,
) -> VideoCatalogEntry:
    """Catalog details for a video, written in the language its viewers read."""
    if llm is None or getattr(llm, "name", "") == "mock":
        return _fallback(title, request)
    prompt = (
        "You are cataloguing an educational video so learners can find it in a public "
        "library. Read what it teaches and write its catalog entry.\n"
        f"Write title, description and level in {language}. Write subject and tags in "
        "English so search works across languages; tags are the concepts, terms and "
        "keywords a learner might search for.\n\n"
        f"CURRENT TITLE: {title}\n"
        f"LEARNER'S REQUEST: {request}\n"
        "SCENES:\n" + "\n".join(f"- {scene}" for scene in scenes) + "\n\n"
        f"NARRATION (excerpt):\n{narration[:6000]}"
    )
    try:
        with usage_stage("catalog"):
            entry = await llm.generate_structured(prompt, VideoCatalogEntry)
    except Exception as error:  # noqa: BLE001 - publishing must still work without the model
        logger.warning("Video catalog description unavailable: %s", error)
        return _fallback(title, request)
    # Trimmed to what the catalog stores.
    entry.title = (entry.title.strip() or title)[:300]
    entry.description = entry.description.strip()[:4000]
    entry.subject = entry.subject.strip()[:120]
    entry.tags = list(dict.fromkeys(tag.strip()[:60] for tag in entry.tags if tag.strip()))[:30]
    entry.level = entry.level.strip()[:80]
    return entry
