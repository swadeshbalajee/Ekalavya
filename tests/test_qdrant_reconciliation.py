from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import SourceChunk, SourceDocument
from scholarmotion.services.index_reconciliation import reconcile_qdrant_index
from scholarmotion.skills.bootstrap import bootstrap_system_knowledge


class FakeEmbedder:
    async def embed(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]


class FakeVectorStore:
    def __init__(self):
        self.config = SimpleNamespace(source_collection="source", skill_collection="skills")
        self.collections = {}

    async def reconcile_collection(self, collection, points):
        self.collections[collection] = points


@pytest.mark.asyncio
async def test_qdrant_reconciliation_rebuilds_source_and_skill_collections(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    vector_store = FakeVectorStore()
    monkeypatch.setattr(
        "scholarmotion.services.index_reconciliation.create_embedding_provider",
        lambda _settings: FakeEmbedder(),
    )

    async with sessions() as session:
        document = SourceDocument(title="A paper", uri="source.pdf", kind="paper", metadata_json={})
        session.add(document)
        await session.flush()
        chunk = SourceChunk(
            document_id=document.id,
            text="Magnetic flux through a surface",
            class_level=12,
            subject="Physics",
            chapter="Electromagnetic induction",
            section="1.1",
            page=1,
            content_type="paragraph",
        )
        session.add(chunk)
        await bootstrap_system_knowledge(session)
        await session.commit()

        counts = await reconcile_qdrant_index(session, Settings(), vector_store)
        await session.commit()

        assert counts["source_chunks"] == 1
        assert counts["skills_and_corrections"] == 8  # seven system skills plus manim-layout
        assert len(vector_store.collections["source"]) == 1
        assert {point["payload"]["kind"] for point in vector_store.collections["skills"]} == {
            "skill"
        }
        refreshed = await session.scalar(select(SourceChunk).where(SourceChunk.id == chunk.id))
        assert refreshed.vector_revision == 1
        await reconcile_qdrant_index(session, Settings(), vector_store)
        await session.commit()
        refreshed = await session.scalar(select(SourceChunk).where(SourceChunk.id == chunk.id))
        assert refreshed.vector_revision == 1

    await engine.dispose()
