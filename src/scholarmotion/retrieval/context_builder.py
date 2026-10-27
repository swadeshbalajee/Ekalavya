"""Formats retrieved chunks into prompt context."""

from __future__ import annotations

from .hybrid_retrieval import RetrievedChunk


def build_context(results: list[RetrievedChunk], *, max_characters: int = 16_000) -> list[dict]:
    output: list[dict] = []
    used = 0
    for item in results:
        text = item.chunk.get("text", "")
        if used + len(text) > max_characters:
            break
        output.append({**item.chunk, "score": item.score, "retrieval_reasons": item.reasons})
        used += len(text)
    return output
