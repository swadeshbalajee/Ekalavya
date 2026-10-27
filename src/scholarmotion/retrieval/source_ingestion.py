"""Turn an uploaded file or a link into an indexed, citable source of a chat.

Uploads, the attach-by-link button and links pasted into the chat all end up
here, so a PDF and a web page are parsed, embedded and indexed the same way.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from scholarmotion.config import Settings
from scholarmotion.persistence.models import SourceChunk, SourceDocument
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantUnavailable, QdrantVectorStore
from scholarmotion.providers import create_embedding_provider
from scholarmotion.retrieval.paper_download import fetch_link
from scholarmotion.retrieval.paper_ingestion import PaperParser, ParsedPaper
from scholarmotion.retrieval.web_page import parse_web_page

logger = logging.getLogger(__name__)


class LinkUnreadable(ValueError):
    """A link the learner gave could not be read; the reason is learner-facing."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


async def index_source(
    session, settings: Settings, document: SourceDocument, parse: Callable[[], ParsedPaper]
) -> None:
    """Parse, embed and index one stored source; a failure marks it failed.

    Runs inside a savepoint: a failed parse or insert rolls back only the
    ingestion, so the stored source is kept and reported instead of erroring.
    """
    uploaded_metadata = dict(document.metadata_json or {})
    try:
        async with session.begin_nested():
            paper = parse()
            vectors = await create_embedding_provider(settings).embed(
                [chunk.text for chunk in paper.chunks]
            )
            document.title = paper.title
            document.authors = paper.authors
            document.metadata_json = {
                **uploaded_metadata,
                **paper.metadata,
                "abstract": paper.abstract,
                "ingestion_status": "complete",
            }
            indexed_chunks: list[tuple[SourceChunk, list[float]]] = []
            for chunk, embedding in zip(paper.chunks, vectors):
                row = SourceChunk(
                    document_id=document.id,
                    section=chunk.section,
                    page=chunk.page,
                    content_type=chunk.content_type,
                    text=chunk.text,
                    equations=[chunk.text] if chunk.content_type == "equation" else [],
                    concept_tags=[],
                    prerequisite_tags=[],
                )
                session.add(row)
                indexed_chunks.append((row, embedding))
            await session.flush()
            try:
                await QdrantVectorStore(QdrantConfig.from_settings(settings)).upsert_source_chunks(
                    [
                        {
                            "id": str(row.id),
                            "vector": embedding,
                            "payload": {
                                "document_id": str(document.id),
                                "project_id": str(document.project_id),
                                "class_level": row.class_level,
                                "subject": row.subject.lower() if row.subject else None,
                                "chapter": row.chapter,
                                "content_type": row.content_type,
                            },
                        }
                        for row, embedding in indexed_chunks
                    ]
                )
                for row, _ in indexed_chunks:
                    row.vector_revision = 1
            except QdrantUnavailable as error:
                document.metadata_json = {
                    **document.metadata_json,
                    "vector_status": "pending",
                    "vector_error": str(error),
                }
    except Exception as exc:  # noqa: BLE001 - preserve source and surface provider/parser errors
        document.metadata_json = {
            **uploaded_metadata,
            "ingestion_status": "failed",
            "ingestion_error": str(exc)[:500],
        }


def parse_stored_pdf(store, key: str, project_id, document_id) -> Callable[[], ParsedPaper]:
    return lambda: PaperParser().parse(
        store.local_path(key),
        asset_dir=store.local_path(f"{project_id}/sources/assets/{document_id}"),
    )


async def _free_key(session, project_id: UUID, filename: str, url: str) -> str:
    key = f"{project_id}/sources/{filename}"
    taken = await session.scalar(
        select(SourceDocument.id).where(
            SourceDocument.project_id == project_id, SourceDocument.uri == key
        )
    )
    if not taken:
        return key
    stem, suffix = Path(filename).stem, Path(filename).suffix
    return f"{project_id}/sources/{stem}-{hashlib.sha256(url.encode()).hexdigest()[:8]}{suffix}"


async def ingest_link(
    session, settings: Settings, store, project_id: UUID, url: str
) -> SourceDocument:
    """Read a PDF or web page link into this chat's sources (once per link).

    Raises LinkUnreadable with a learner-facing reason when the link cannot be
    fetched or holds no readable text; nothing is substituted for it.
    """
    url = url.strip()
    for existing in (
        await session.scalars(select(SourceDocument).where(SourceDocument.project_id == project_id))
    ).all():
        metadata = existing.metadata_json or {}
        if metadata.get("source_url") == url and metadata.get("ingestion_status") == "complete":
            return existing
    try:
        # Plain-http links are read over HTTPS; only public HTTPS hosts are fetched.
        fetched = await fetch_link("https://" + url[7:] if url.startswith("http://") else url)
    except ValueError as error:
        raise LinkUnreadable(url, str(error)) from error
    except httpx.HTTPStatusError as error:
        raise LinkUnreadable(
            url, f"the site answered with HTTP {error.response.status_code}"
        ) from error
    except httpx.HTTPError as error:
        raise LinkUnreadable(
            url, f"the site could not be reached ({type(error).__name__})"
        ) from error
    key = await _free_key(session, project_id, fetched.filename, url)
    store.put_bytes(key, fetched.data, overwrite=True)
    document = SourceDocument(
        project_id=project_id,
        kind="paper" if fetched.kind == "pdf" else "web_page",
        title=Path(fetched.filename).stem,
        uri=key,
        metadata_json={
            "content_type": "application/pdf" if fetched.kind == "pdf" else "text/html",
            "source_url": url,
        },
    )
    session.add(document)
    await session.flush()
    if fetched.kind == "pdf":
        parse = parse_stored_pdf(store, key, project_id, document.id)
    else:
        html = fetched.text()
        parse = lambda: parse_web_page(html, fetched.url)
    await index_source(session, settings, document, parse)
    metadata = document.metadata_json or {}
    if metadata.get("ingestion_status") != "complete":
        reason = metadata.get("ingestion_error") or "its text could not be read"
        await session.delete(document)
        await session.flush()
        raise LinkUnreadable(url, reason)
    return document
