"""Narration-language labels must render in a font that has the script's glyphs."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import PIL.Image

from scholarmotion.manim_runtime.sandbox import _GLYPH_GUARD


def _guard(monkeypatch, installed: set[str]) -> dict:
    # Run the render prelude against a stand-in manim so nothing global is patched.
    monkeypatch.setitem(sys.modules, "manim", SimpleNamespace())
    monkeypatch.setattr(PIL.Image, "fromarray", PIL.Image.fromarray)
    namespace: dict = {}
    exec(_GLYPH_GUARD, namespace)  # noqa: S102 - the prelude is our own source
    namespace["_sm_installed_fonts"] = installed
    return namespace


def test_indic_text_gets_a_font_that_covers_its_script(monkeypatch):
    guard = _guard(monkeypatch, {"Nirmala UI", "Yu Gothic"})
    pick = guard["_sm_script_font"]
    assert pick("ஒளிச்சேர்க்கை") == "Nirmala UI"
    assert pick("न्यूटन का तीसरा नियम") == "Nirmala UI"
    assert pick("ニュートン") == "Yu Gothic"
    assert pick("English label") is None


def test_linux_noto_fonts_are_used_when_windows_fonts_are_absent(monkeypatch):
    guard = _guard(monkeypatch, {"Noto Sans Tamil"})
    assert guard["_sm_script_font"]("தமிழ்") == "Noto Sans Tamil"


def test_text_constructor_receives_the_script_font_unless_one_was_chosen(monkeypatch):
    guard = _guard(monkeypatch, {"Nirmala UI"})
    seen = []

    class Text:
        def __init__(self, text, **kwargs):
            seen.append(kwargs.get("font"))

    guard["_sm_guard_text"](Text)
    Text("தமிழ்")
    Text("தமிழ்", font="Latha")
    Text("plain")
    assert seen == ["Nirmala UI", "Latha", None]
