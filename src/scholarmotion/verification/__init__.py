"""Checks on rendered scenes: render, layout, math, pedagogy, grounding."""

from .aggregator import aggregate_reports
from .math import verify_equivalence

__all__ = ["aggregate_reports", "verify_equivalence"]
