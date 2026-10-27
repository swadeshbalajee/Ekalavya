"""HTML <track> markup that attaches WebVTT captions to the lesson video."""

from __future__ import annotations

import html

_LANGUAGE_CODES = {
    "arabic": "ar",
    "bengali": "bn",
    "english": "en",
    "french": "fr",
    "hindi": "hi",
    "japanese": "ja",
    "marathi": "mr",
    "spanish": "es",
    "tamil": "ta",
    "telugu": "te",
}


def subtitle_track_markup(url: str, language: str = "English") -> str:
    """Build the default browser subtitle track for the video's VTT captions."""
    locale = _LANGUAGE_CODES.get(language.casefold(), "en")
    return (
        f'<track kind="subtitles" src="{html.escape(url, quote=True)}" '
        f'srclang="{locale}" label="Subtitles" default>'
    )
