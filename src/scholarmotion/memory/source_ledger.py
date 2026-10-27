"""Ledger linking lesson claims to the sources that support them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class LedgerClaim:
    claim_id: str
    claim: str
    sources: tuple[dict, ...]
    confidence: float
    claim_type: str


class SourceLedger:
    allowed_types: ClassVar[frozenset[str]] = frozenset(
        {"direct", "derived", "inferred", "teaching_analogy"}
    )

    def __init__(self):
        self.claims: dict[str, LedgerClaim] = {}

    def add(self, claim: LedgerClaim) -> None:
        if claim.claim_type not in self.allowed_types:
            raise ValueError(f"unknown claim type: {claim.claim_type}")
        if claim.claim_type in {"direct", "derived"} and not claim.sources:
            raise ValueError("direct and derived claims require source provenance")
        if not 0 <= claim.confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        self.claims[claim.claim_id] = claim

    def require(self, claim_ids: list[str]) -> list[LedgerClaim]:
        missing = [claim_id for claim_id in claim_ids if claim_id not in self.claims]
        if missing:
            raise KeyError(f"unknown source claims: {missing}")
        return [self.claims[claim_id] for claim_id in claim_ids]
