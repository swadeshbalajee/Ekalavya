"""Per-build token and dollar accounting for model calls.

A build opens a :class:`UsageLedger` with :func:`track_usage`; every provider
call made inside it (including concurrent scene tasks, which inherit the
context) records the tokens and the cost OpenRouter reports for that call.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageEntry:
    purpose: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    # Characters spoken or translated, for services billed per character.
    characters: int = 0


@dataclass
class UsageLedger:
    entries: list[UsageEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, entry: UsageEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def estimate_missing_costs(self, prices: dict[str, dict[str, float]]) -> None:
        """Price calls whose response carried no cost, from per-token prices.

        ``prices`` maps model id to {"prompt", "completion", "input_cache_read"}
        dollars per token, as OpenRouter's public model list reports them.
        """
        with self._lock:
            for entry in self.entries:
                price = prices.get(entry.model) or prices.get(entry.model.split(":")[0])
                if entry.cost_usd is not None or not price:
                    continue
                cached = min(entry.cached_tokens, entry.input_tokens)
                entry.cost_usd = (
                    (entry.input_tokens - cached) * price.get("prompt", 0.0)
                    + cached * price.get("input_cache_read", price.get("prompt", 0.0))
                    + entry.output_tokens * price.get("completion", 0.0)
                )

    def summary(self) -> dict[str, Any]:
        """Totals overall and per purpose/model, rounded for display."""
        with self._lock:
            entries = list(self.entries)

        def totals(items: list[UsageEntry]) -> dict[str, Any]:
            return {
                "calls": len(items),
                "input_tokens": sum(item.input_tokens for item in items),
                "output_tokens": sum(item.output_tokens for item in items),
                "cached_tokens": sum(item.cached_tokens for item in items),
                "cache_write_tokens": sum(item.cache_write_tokens for item in items),
                "reasoning_tokens": sum(item.reasoning_tokens for item in items),
                "cost_usd": round(sum(item.cost_usd or 0.0 for item in items), 4),
                "unpriced_calls": sum(1 for item in items if item.cost_usd is None),
            }

        groups: dict[tuple[str, str], list[UsageEntry]] = {}
        for entry in entries:
            groups.setdefault((entry.purpose, entry.model), []).append(entry)
        return {
            **totals(entries),
            "by_purpose": [
                {"purpose": purpose, "model": model, **totals(items)}
                for (purpose, model), items in sorted(
                    groups.items(), key=lambda pair: -sum(i.cost_usd or 0 for i in pair[1])
                )
            ],
        }


_LEDGER: contextvars.ContextVar[UsageLedger | None] = contextvars.ContextVar(
    "scholarmotion_usage_ledger", default=None
)


_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "scholarmotion_usage_stage", default=None
)


@contextmanager
def usage_stage(name: str) -> Iterator[None]:
    """Label model calls made inside this block with a pipeline stage."""
    token = _STAGE.set(name)
    try:
        yield
    finally:
        _STAGE.reset(token)


@contextmanager
def track_usage() -> Iterator[UsageLedger]:
    ledger = UsageLedger()
    token = _LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER.reset(token)


def current_ledger() -> UsageLedger | None:
    return _LEDGER.get()


def _field(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    found = getattr(value, name, None)
    if found is None:
        found = (getattr(value, "model_extra", None) or {}).get(name)
    return found


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record_usage(response: Any, *, purpose: str, model: str) -> None:
    """Record a Chat Completions or Responses API result's usage, if tracked."""
    ledger = _LEDGER.get()
    if ledger is None:
        return
    usage = _field(response, "usage")
    if usage is None:
        return
    input_tokens = _field(usage, "prompt_tokens")
    if input_tokens is None:
        input_tokens = _field(usage, "input_tokens")
    output_tokens = _field(usage, "completion_tokens")
    if output_tokens is None:
        output_tokens = _field(usage, "output_tokens")
    prompt_details = _field(usage, "prompt_tokens_details") or _field(usage, "input_tokens_details")
    output_details = _field(usage, "completion_tokens_details") or _field(
        usage, "output_tokens_details"
    )
    cost = _field(usage, "cost")
    try:
        cost = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost = None
    stage = _STAGE.get()
    ledger.add(
        UsageEntry(
            purpose=f"{stage}:{purpose}" if stage else purpose,
            # The requested id, which matches the price list; responses may
            # report a dated snapshot name instead.
            model=model,
            input_tokens=_int(input_tokens),
            output_tokens=_int(output_tokens),
            cached_tokens=_int(_field(prompt_details, "cached_tokens")),
            cache_write_tokens=_int(_field(prompt_details, "cache_write_tokens")),
            reasoning_tokens=_int(_field(output_details, "reasoning_tokens")),
            cost_usd=cost,
        )
    )


def record_service_usage(
    *,
    purpose: str,
    model: str,
    cost_usd: float | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    characters: int = 0,
) -> None:
    """Record a non-LLM service call (machine translation, TTS), if tracked."""
    ledger = _LEDGER.get()
    if ledger is None:
        return
    stage = _STAGE.get()
    ledger.add(
        UsageEntry(
            purpose=f"{stage}:{purpose}" if stage else purpose,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            characters=characters,
            cost_usd=cost_usd,
        )
    )
