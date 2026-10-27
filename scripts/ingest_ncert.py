"""Bulk-index a directory of NCERT textbook PDFs as shared sources.

Usage: python scripts/ingest_ncert.py path/to/ncert-pdfs
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import SourceChunk, SourceDocument
from scholarmotion.persistence.qdrant import QdrantConfig, QdrantVectorStore
from scholarmotion.providers import create_embedding_provider
from scholarmotion.retrieval.ncert_ingestion import ingest_ncert_directory


async def run(directory: Path) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    await database.create_all()
    records = ingest_ncert_directory(directory)
    embeddings = await create_embedding_provider(settings).embed([item["text"] for item in records])
    qdrant_chunks: list[dict] = []
    async with database.sessions() as session:
        documents: dict[str, SourceDocument] = {}
        indexed_rows: list[SourceChunk] = []
        for record, embedding in zip(records, embeddings):
            path = record.pop("path")
            if path not in documents:
                document = SourceDocument(
                    kind="ncert",
                    title=Path(path).stem,
                    authors=["NCERT"],
                    uri=path,
                    metadata_json={"copyright": "Source PDF supplied by user; not redistributed."},
                )
                session.add(document)
                await session.flush()
                documents[path] = document
            allowed = {
                key: record.get(key)
                for key in (
                    "class_level",
                    "subject",
                    "book",
                    "chapter",
                    "section",
                    "page",
                    "content_type",
                    "text",
                    "equations",
                    "examples",
                    "definitions",
                    "concept_tags",
                    "prerequisite_tags",
                )
            }
            chunk = SourceChunk(document_id=documents[path].id, **allowed)
            session.add(chunk)
            await session.flush()
            indexed_rows.append(chunk)
            qdrant_chunks.append(
                {
                    "id": str(chunk.id),
                    "vector": embedding,
                    "payload": {
                        "document_id": str(documents[path].id),
                        "class_level": allowed["class_level"],
                        "subject": (allowed["subject"] or "").lower() or None,
                        "chapter": allowed["chapter"],
                        "content_type": allowed["content_type"],
                    },
                }
            )
        await QdrantVectorStore(QdrantConfig.from_settings(settings)).upsert_source_chunks(qdrant_chunks)
        for chunk in indexed_rows:
            chunk.vector_revision = 1
        await session.commit()
    await database.close()
    print(f"Ingested {len(records)} chunks from {directory}")

    print(f"Indexed {len(qdrant_chunks)} chunks in Qdrant")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.directory))
