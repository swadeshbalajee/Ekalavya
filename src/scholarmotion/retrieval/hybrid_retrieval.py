from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: dict
    score: float
    reasons: tuple[str, ...]


class HybridRetriever:
    """Small-corpus fallback used only if the Qdrant index is unavailable."""

    def __init__(self, embedding_provider):
        self.embedding_provider = embedding_provider

    async def search(
        self,
        query: str,
        chunks: list[dict],
        *,
        metadata: dict | None = None,
        expanded_concepts: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievedChunk]:
        filtered = [
            chunk
            for chunk in chunks
            if all(
                value is None or chunk.get(key) == value for key, value in (metadata or {}).items()
            )
        ]
        if not filtered:
            return []
        vectors = await self.embedding_provider.embed(
            [query, *[chunk.get("text", "") for chunk in filtered]]
        )
        query_vector, chunk_vectors = vectors[0], vectors[1:]
        query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        expanded = {item.lower() for item in (expanded_concepts or [])}
        results = []
        for chunk, vector in zip(filtered, chunk_vectors):
            text_terms = set(re.findall(r"[a-z0-9]+", chunk.get("text", "").lower()))
            lexical = len(query_terms & text_terms) / max(1, len(query_terms))
            cosine = sum(left * right for left, right in zip(query_vector, vector))
            tags = {item.lower() for item in chunk.get("concept_tags", [])}
            concept = len(expanded & tags) / max(1, len(expanded)) if expanded else 0
            phrase = 1 if query.lower() in chunk.get("text", "").lower() else 0
            score = 0.45 * cosine + 0.35 * lexical + 0.15 * concept + 0.05 * phrase
            reasons = tuple(
                name
                for name, value in (
                    ("vector", cosine),
                    ("full_text", lexical),
                    ("concept_graph", concept),
                    ("phrase", phrase),
                )
                if value > 0
            )
            results.append(RetrievedChunk(chunk, score, reasons))
        return sorted(
            results, key=lambda item: (item.score, len(item.chunk.get("text", ""))), reverse=True
        )[:limit]
