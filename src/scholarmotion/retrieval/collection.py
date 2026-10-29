"""Collect a lesson's material: several focused queries, run in parallel.

The planner model splits the lesson brief into distinct search queries (one per part
of the topic). The library and the learner's sources are searched with all of them
at once, and the web searches run in parallel. How many of each is the learner's
research setting.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from scholarmotion.observability.usage import usage_stage
from scholarmotion.retrieval.paper_download import MAX_PAGE_BYTES, fetch_link
from scholarmotion.retrieval.web_page import parse_web_page
from scholarmotion.retrieval.web_research import web_research

logger = logging.getLogger(__name__)


class SearchPlan(BaseModel):
    queries: list[str] = Field(default_factory=list)


async def plan_queries(llm, brief: str, first: str, count: int) -> list[str]:
    """Up to ``count`` distinct queries; the first is the planner's own retrieval query."""
    first = (first or brief).strip()
    if count <= 1 or getattr(llm, "name", "") == "mock" or not hasattr(llm, "generate_structured"):
        return [first]
    prompt = (
        "You plan the research for a lesson. Write up to "
        f"{count} distinct search queries that together cover what this lesson needs: each "
        "query targets a different part, such as a key definition, the mechanism, the "
        "mathematics, a standard worked example, a common misconception, or a named part "
        "of a given source. Write each as a short search-engine query, not a sentence to "
        "the learner. Fewer queries are fine when the lesson is narrow.\n\n"
        f"LESSON BRIEF: {brief}\nMAIN QUERY: {first}"
    )
    try:
        with usage_stage("research_plan"):
            plan = await llm.generate_structured(prompt, SearchPlan)
    except Exception as error:  # noqa: BLE001 - one query is a working plan
        logger.warning("Query planning failed; using the main query: %r", error)
        return [first]
    queries = [first, *(query.strip() for query in plan.queries)]
    return [query for query in dict.fromkeys(queries) if query][:count]


async def _read_page(url: str, index: int) -> list[dict]:
    """One web result read in full, as retrieval-style chunks."""
    try:
        fetched = await fetch_link(url, max_bytes=MAX_PAGE_BYTES)
        if fetched.kind == "pdf":
            return []
        parsed = parse_web_page(fetched.text(), fetched.url)
    except Exception as error:  # noqa: BLE001 - an unreadable page is skipped
        logger.info("Skipping web page %s: %s", url, error)
        return []
    return [
        {
            "id": f"webpage-{index}-{number}",
            "document_id": f"webpage-{index}",
            "document_title": parsed.title,
            "document_kind": "web",
            "content_type": chunk.content_type,
            "section": chunk.section,
            "text": chunk.text,
            "url": url,
            "concept_tags": [],
            "score": 0.5,
        }
        for number, chunk in enumerate(parsed.chunks, 1)
    ]


async def search_web(
    llm,
    queries: list[str],
    *,
    results_per_search: int,
    pages_to_read: int,
    reasoning_effort: str = "medium",
) -> list[dict]:
    """One web search per query, all at once; then the top pages are read in full."""
    if not queries:
        return []
    with usage_stage("web_research"):
        answers = await asyncio.gather(
            *(
                web_research(
                    llm, query, reasoning_effort=reasoning_effort, max_results=results_per_search
                )
                for query in queries
            )
        )
    facts: list[dict] = []
    for number, chunks in enumerate(answers, 1):
        for chunk in chunks:
            facts.append({**chunk, "id": f"web-{number}-{chunk['id'].removeprefix('web-')}"})
    urls = list(dict.fromkeys(chunk["url"] for chunk in facts if chunk.get("url")))
    pages = await asyncio.gather(
        *(_read_page(url, index) for index, url in enumerate(urls[:pages_to_read], 1))
    )
    return facts + [chunk for page in pages for chunk in page]
