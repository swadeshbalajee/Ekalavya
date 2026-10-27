"""Classifies retrieved source chunks into knowledge cards (definition, equation, figure, example)."""

from __future__ import annotations

import re

from scholarmotion.schemas import KnowledgeCard


def gather_knowledge(chunks: list[dict]) -> list[KnowledgeCard]:
    cards: list[KnowledgeCard] = []
    for index, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "").strip()
        if not text:
            continue
        source_id = str(chunk.get("id") or chunk.get("source_id") or f"source_{index}")
        kind = (
            "equation"
            if chunk.get("content_type") == "equation" or re.search(r"\$[^$]+\$|=", text)
            else "definition"
            if re.search(r"\b(?:is defined as|means|refers to)\b", text, re.IGNORECASE)
            else "figure"
            if chunk.get("content_type") == "figure"
            else "example"
            if "example" in text.lower()
            else "fact"
        )
        cards.append(
            KnowledgeCard(
                card_id=f"card_{index:03d}",
                kind=kind,
                content=text,
                source_ids=[source_id],
                confidence=float(chunk.get("score", 1)),
            )
        )
    return cards
