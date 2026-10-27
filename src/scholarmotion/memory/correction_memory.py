"""Stores and matches learner corrections for reuse in later scenes."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.persistence.models import CorrectionEntry


@dataclass(frozen=True)
class Correction:
    correction_id: str
    category: str
    trigger_conditions: str
    anti_pattern: str
    required_behavior: str
    recommended_fix: str
    evidence_count: int
    confidence: float
    tags: tuple[str, ...]
    applicable_model: str = "any"
    status: str = "active"

    def prompt_fragment(self) -> str:
        return (
            f"{self.correction_id} [{self.category}]\nTrigger: {self.trigger_conditions}\n"
            f"Required: {self.required_behavior}\nFix: {self.recommended_fix}"
        )


class CorrectionMemory:
    """Versioned PostgreSQL correction memory; Qdrant may index it for recall."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def retrieve(
        self,
        *,
        tags: list[str],
        primitives: list[str] | None = None,
        failures: list[str] | None = None,
        model: str = "any",
        limit: int = 5,
    ) -> list[Correction]:
        terms = {term.lower() for term in [*tags, *(primitives or []), *(failures or [])]}
        entries = (
            await self.session.scalars(
                select(CorrectionEntry).where(CorrectionEntry.status == "active")
            )
        ).all()
        scored: list[tuple[float, Correction]] = []
        for entry in entries:
            if entry.applicable_model not in {"any", model}:
                continue
            haystack = {entry.category.lower(), *[tag.lower() for tag in entry.tags]}
            score = (
                len(terms & haystack)
                + 0.2 * entry.confidence
                + 0.01 * min(entry.evidence_count, 20)
            )
            if score > 0.1:
                scored.append(
                    (
                        score,
                        Correction(
                            entry.correction_key,
                            entry.category,
                            entry.trigger_conditions,
                            entry.anti_pattern,
                            entry.required_behavior,
                            entry.recommended_fix,
                            entry.evidence_count,
                            entry.confidence,
                            tuple(entry.tags),
                            entry.applicable_model,
                            entry.status,
                        ),
                    )
                )
        return [
            entry for _, entry in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
        ]

    async def append_validated(self, entry: Correction, *, validation_tests: list[str]) -> None:
        if entry.evidence_count < 2 or entry.confidence < 0.8:
            raise ValueError("corrections need repeated, high-confidence evidence")
        existing = await self.session.scalar(
            select(CorrectionEntry.id).where(CorrectionEntry.correction_key == entry.correction_id)
        )
        if existing:
            raise ValueError("correction ID already exists")
        self.session.add(
            CorrectionEntry(
                correction_key=entry.correction_id,
                category=entry.category,
                trigger_conditions=entry.trigger_conditions,
                anti_pattern=entry.anti_pattern,
                required_behavior=entry.required_behavior,
                recommended_fix=entry.recommended_fix,
                evidence_count=entry.evidence_count,
                confidence=entry.confidence,
                tags=list(entry.tags),
                applicable_model=entry.applicable_model,
                status=entry.status,
                validation_tests=validation_tests,
            )
        )
