"""Source-chunk retrieval from the Qdrant vector store."""

from __future__ import annotations

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
        vector = (await self.embeddings.embed([query]))[0]
        results = await self.vector_store.query_sources(
            vector,
            filters={"class_level": class_level, "subject": subject.lower() if subject else None},
            limit=limit,
        )
        if not results:
            return []
        ids = [item[0] for item in results]
        rows = (
            await session.execute(
                select(SourceChunk, SourceDocument)
                .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                .where(SourceChunk.id.in_(ids))
            )
        ).all()
        by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}
        retrieved: list[RetrievedChunk] = []
        for chunk_id, score, _payload in results:
            item = by_id.get(chunk_id)
            if item is None:
                continue
            chunk, document = item
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
                    score,
                    ("qdrant_cosine",),
                )
            )
        return retrieved
