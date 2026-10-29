"""Per-learner research depth: how much material a lesson collects and keeps in context.

Every value has a default (the behaviour before these settings existed, except web
searches) and a hard range the server enforces, so a crafted request cannot ask for
hundreds of searches. Raising a value usually means more model calls or longer
prompts, so the web app shows the expected cost and time of each change.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Knob:
    key: str
    label: str
    help: str
    default: int
    minimum: int
    maximum: int
    # Rough marginal cost and time of one step up, used when usage history is thin.
    cost_per_step_usd: float
    seconds_per_step: float
    # The usage purpose whose measured average replaces the rough cost, if any.
    measured_by: str | None = None


KNOBS: tuple[Knob, ...] = (
    Knob(
        "web_searches",
        "Web searches per video",
        "Separate web searches, each on a different part of the topic, run in parallel.",
        3,
        1,
        10,
        0.027,
        2.0,
        measured_by="web_answer",
    ),
    Knob(
        "web_results",
        "Results per web search",
        "How many web results each search reads.",
        5,
        1,
        10,
        0.004,
        0.5,
    ),
    Knob(
        "web_pages_read",
        "Web pages read in full",
        "Top web pages fetched and read in full instead of only their search summary.",
        0,
        0,
        10,
        0.01,
        3.0,
    ),
    Knob(
        "search_queries",
        "Library search queries",
        "Separate searches of your sources and the library, one per part of the topic.",
        1,
        1,
        8,
        0.002,
        0.3,
    ),
    Knob(
        "library_top_k",
        "Passages per library search",
        "How many of the best-matching passages each library search returns.",
        20,
        5,
        60,
        0.0006,
        0.1,
    ),
    Knob(
        "full_read_chunks",
        "Read a whole source up to (chunks)",
        "A source with at most this many chunks is read whole; a longer one by its best passages.",
        60,
        20,
        200,
        0.0004,
        0.05,
    ),
    Knob(
        "one_pass_kchars",
        "Read in one pass up to (thousand characters)",
        "Above this, the material is split into groups that are read in parallel and merged.",
        80,
        20,
        300,
        0.0008,
        0.2,
    ),
    Knob(
        "reference_lookups",
        "Lookups per writing step",
        "How many times the script writer and storyboard may look up the full source text.",
        2,
        0,
        5,
        0.006,
        4.0,
    ),
    Knob(
        "chat_turns_kept",
        "Chat turns kept word for word",
        "Older turns are kept as a running summary.",
        7,
        3,
        15,
        0.0005,
        0.2,
    ),
)

KNOB_BY_KEY = {knob.key: knob for knob in KNOBS}


class ResearchSettings(BaseModel):
    web_searches: int = KNOB_BY_KEY["web_searches"].default
    web_results: int = KNOB_BY_KEY["web_results"].default
    web_pages_read: int = KNOB_BY_KEY["web_pages_read"].default
    search_queries: int = KNOB_BY_KEY["search_queries"].default
    library_top_k: int = KNOB_BY_KEY["library_top_k"].default
    full_read_chunks: int = KNOB_BY_KEY["full_read_chunks"].default
    one_pass_kchars: int = KNOB_BY_KEY["one_pass_kchars"].default
    reference_lookups: int = KNOB_BY_KEY["reference_lookups"].default
    chat_turns_kept: int = KNOB_BY_KEY["chat_turns_kept"].default

    @property
    def one_pass_chars(self) -> int:
        return self.one_pass_kchars * 1000


def resolve(stored: dict | None) -> ResearchSettings:
    """Stored values clamped into range; unknown keys and bad values fall back to defaults."""
    values = {}
    for knob in KNOBS:
        raw = (stored or {}).get(knob.key, knob.default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = knob.default
        values[knob.key] = min(max(value, knob.minimum), knob.maximum)
    return ResearchSettings(**values)


def describe(measured_costs: dict[str, float] | None = None) -> list[dict]:
    """What the settings page needs: labels, ranges, defaults and per-step cost and time."""
    measured_costs = measured_costs or {}
    return [
        {
            "key": knob.key,
            "label": knob.label,
            "help": knob.help,
            "default": knob.default,
            "min": knob.minimum,
            "max": knob.maximum,
            "cost_per_step_usd": measured_costs.get(knob.measured_by or "", knob.cost_per_step_usd),
            "seconds_per_step": knob.seconds_per_step,
        }
        for knob in KNOBS
    ]
