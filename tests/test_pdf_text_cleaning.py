"""Extracted PDF text never carries control characters into the database."""

from __future__ import annotations

from types import SimpleNamespace

from scholarmotion.retrieval import paper_ingestion
from scholarmotion.retrieval.paper_ingestion import PaperParser, clean_text


def test_clean_text_drops_nul_and_control_characters():
    assert clean_text("as de\x00ned\x07 here") == "as dened here"
    assert clean_text("line one\nline\ttwo") == "line one\nline\ttwo"
    assert clean_text(None) == ""


def test_parsed_chunks_have_no_nul(monkeypatch, tmp_path):
    pages = [
        SimpleNamespace(
            extract_text=lambda: "Intro\x00\n\nConsciousness (as de\x00ned) here.", images=[]
        )
    ]
    reader = SimpleNamespace(pages=pages, metadata=SimpleNamespace(title="AI\x00 models"))
    monkeypatch.setattr(paper_ingestion, "PdfReader", lambda _path: reader)
    paper = PaperParser().parse(tmp_path / "blog.pdf")
    assert "\x00" not in paper.title
    assert paper.chunks
    assert all("\x00" not in chunk.text for chunk in paper.chunks)
