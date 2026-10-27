"""Bulk ingestion of NCERT textbook PDFs with subject/grade metadata."""

from __future__ import annotations

import re
from pathlib import Path

from .paper_ingestion import PaperParser


def infer_ncert_metadata(path: str | Path) -> dict:
    file = Path(path)
    joined = " ".join(file.parts).lower()
    level = re.search(r"(?:class|grade)[ _-]?(8|9|10|11|12)\b", joined)
    subject = next(
        (
            name
            for name in ("physics", "chemistry", "mathematics", "biology", "science")
            if name in joined
        ),
        None,
    )
    return {
        "class_level": int(level.group(1)) if level else None,
        "subject": subject,
        "book": file.stem,
    }


def ingest_ncert_directory(directory: str | Path) -> list[dict]:
    parser = PaperParser()
    records: list[dict] = []
    for pdf in sorted(Path(directory).rglob("*.pdf")):
        metadata = infer_ncert_metadata(pdf)
        parsed = parser.parse(pdf)
        for chunk in parsed.chunks:
            records.append(
                {
                    **metadata,
                    "path": str(pdf),
                    "chapter": chunk.section,
                    "section": chunk.section,
                    "page": chunk.page,
                    "content_type": chunk.content_type,
                    "text": chunk.text,
                    "equations": [chunk.text] if chunk.content_type == "equation" else [],
                    "examples": [chunk.text] if "example" in chunk.text.lower() else [],
                    "definitions": [chunk.text] if "defined as" in chunk.text.lower() else [],
                    "concept_tags": _tags(chunk.text),
                    "prerequisite_tags": [],
                }
            )
    return records


def _tags(text: str) -> list[str]:
    stop = {"the", "and", "with", "from", "that", "this", "into", "where", "when"}
    return sorted(
        {word.lower() for word in re.findall(r"[A-Za-z]{4,}", text) if word.lower() not in stop}
    )[:20]
