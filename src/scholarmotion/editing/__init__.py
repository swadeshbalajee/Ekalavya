"""Selective-edit dependency tracking between scene artifacts."""

from .dependencies import InvalidationPlan, invalidation_for, propagate_staleness

__all__ = [
    "InvalidationPlan",
    "invalidation_for",
    "propagate_staleness",
]
