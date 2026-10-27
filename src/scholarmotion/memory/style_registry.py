"""Default visual style for lessons and per-project overrides."""

from __future__ import annotations

DEFAULT_STYLE = {
    "background": "#0B1020",
    "foreground": "#F7F8FC",
    "accent": "#55D6BE",
    "warning": "#FFCA3A",
    "font": "sans-serif",
    "subtitle_safe_height": 1.0,
    "title_max_height": 0.7,
    "motion": "purposeful",
}


def merge_style(overrides: dict | None = None) -> dict:
    return {**DEFAULT_STYLE, **(overrides or {})}
