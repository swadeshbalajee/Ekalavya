"""Web grounding for requests the saved sources and vector library do not cover."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def supports_web_search(llm) -> bool:
    return getattr(llm, "name", "") != "mock" and hasattr(llm, "generate_text_with_sources")


async def web_research(
    llm, topic: str, *, reasoning_effort: str = "medium", max_results: int = 5
) -> list[dict]:
    """Return retrieval-style chunks of web-sourced teaching facts with citations.

    The chunks use the same shape as vector-store results so the knowledge
    gatherer, curriculum and grounding verifier treat them uniformly.
    """
    if not supports_web_search(llm):
        return []
    prompt = (
        "Research this topic for a student lesson using current, reputable sources "
        "(textbooks, university pages, original papers, encyclopedias). Return 6 to 10 "
        "numbered lines. Each line is one precise, self-contained fact, definition, "
        "standard equation (in plain notation), worked numeric example, or common "
        "misconception, followed by the source title in square brackets. No preamble.\n\n"
        f"TOPIC: {topic}"
    )
    try:
        text, sources = await llm.generate_text_with_sources(
            prompt,
            temperature=0,
            reasoning_effort=reasoning_effort,
            web_search=True,
            max_results=max_results,
        )
    except Exception as error:  # noqa: BLE001 - web grounding is best effort
        logger.warning("Web research unavailable for %r: %s", topic, error)
        return []
    chunks = []
    for index, line in enumerate(text.splitlines()):
        fact = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip()
        if len(fact) < 20:
            continue
        cited = re.search(r"\[([^\]]+)\]\s*$", fact)
        title = cited.group(1).strip() if cited else (sources[0]["title"] if sources else "Web")
        title = title.replace("*", "").replace("_", " ").strip() or "Web"
        url = next((s["url"] for s in sources if s["title"] == title), None) or (
            sources[0]["url"] if sources else ""
        )
        chunks.append(
            {
                "id": f"web-{index + 1}",
                "document_id": "web",
                "document_title": title,
                "document_kind": "web",
                "content_type": "fact",
                "text": fact,
                "url": url,
                "concept_tags": [],
                "score": 0.5,
            }
        )
    return chunks


def format_sources(sources: list[dict]) -> str:
    if not sources:
        return ""
    lines = [f"{index}. [{s['title']}]({s['url']})" for index, s in enumerate(sources[:6], 1)]
    return "\n\n**Sources**\n" + "\n".join(lines)
