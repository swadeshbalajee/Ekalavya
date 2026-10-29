"""Source-chunk retrieval from the Qdrant vector store."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from scholarmotion.persistence.models import SourceChunk, SourceDocument
from scholarmotion.persistence.qdrant import QdrantVectorStore
from scholarmotion.retrieval.hybrid_retrieval import RetrievedChunk


class QdrantHybridRetriever:
    """Vector-first retrieval with PostgreSQL hydration and provenance."""

    def __init__(self, embeddings, vector_store: QdrantVectorStore):
        self.embeddings = embeddings
        self.vector_store = vector_store

    async def search(
        self,
        session,
        query: str,
        *,
        project_id: str | None = None,
        class_level: int | None = None,
        subject: str | None = None,
        limit: int = 20,
    ) -> list[RetrievedChunk]:
        return await self.search_many(
            session,
            [query],
            project_id=project_id,
            class_level=class_level,
            subject=subject,
            limit=limit,
        )

    async def search_many(
        self,
        session,
        queries: list[str],
        *,
        project_id: str | None = None,
        class_level: int | None = None,
        subject: str | None = None,
        limit: int = 20,
    ) -> list[RetrievedChunk]:
        """Several queries at once: one embedding batch, parallel Qdrant searches, one
        PostgreSQL read. Each passage keeps its best score across the queries.

        Only this chat's sources and the shared library are searched, so other chats'
        private sources never crowd out the results.
        """
        queries = [query for query in dict.fromkeys(q.strip() for q in queries) if query]
        if not queries:
            return []
        vectors = await self.embeddings.embed(queries)
        filters = {
            "class_level": class_level,
            "subject": subject.lower() if subject else None,
            # Library chunks carry no project id (older ones the text "None").
            "project_id": [project_id, "None", None] if project_id else None,
        }
        answers = await asyncio.gather(
            *(
                self.vector_store.query_sources(vector, filters=filters, limit=limit)
                for vector in vectors
            )
        )
        best: dict[str, float] = {}
        for results in answers:
            for chunk_id, score, _payload in results:
                best[chunk_id] = max(score, best.get(chunk_id, score))
        if not best:
            return []
        rows = (
            await session.execute(
                select(SourceChunk, SourceDocument)
                .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                .where(SourceChunk.id.in_([UUID(chunk_id) for chunk_id in best]))
            )
        ).all()
        retrieved: list[RetrievedChunk] = []
        for chunk, document in rows:
            if project_id and document.project_id and str(document.project_id) != project_id:
                continue
            retrieved.append(
                RetrievedChunk(
                    {
                        "id": str(chunk.id),
                        "document_id": str(document.id),
                        "document_title": document.title,
                        "document_kind": document.kind,
                        "class_level": chunk.class_level,
                        "subject": chunk.subject,
                        "chapter": chunk.chapter,
                        "section": chunk.section,
                        "page": chunk.page,
                        "content_type": chunk.content_type,
                        "text": chunk.text,
                        "concept_tags": chunk.concept_tags,
                    },
                    best[str(chunk.id)],
                    ("qdrant_cosine",),
                )
            )
        return sorted(retrieved, key=lambda item: item.score, reverse=True)
