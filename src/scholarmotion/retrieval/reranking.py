"""Heuristic reranking that favours definitions, equations, and examples."""

from __future__ import annotations

from .hybrid_retrieval import RetrievedChunk


def heuristic_rerank(
    results: list[RetrievedChunk],
    *,
    preferred_content_types: tuple[str, ...] = ("definition", "equation", "example"),
) -> list[RetrievedChunk]:
    return sorted(
        results,
        key=lambda item: (
            item.score + (0.08 if item.chunk.get("content_type") in preferred_content_types else 0)
        ),
        reverse=True,
    )
