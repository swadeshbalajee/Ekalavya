"""Qdrant is a rebuildable retrieval index; PostgreSQL remains authoritative."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scholarmotion.config import Settings


class QdrantUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class QdrantConfig:
    url: str
    api_key: str | None
    source_collection: str
    skill_collection: str
    dimensions: int

    @classmethod
    def from_settings(cls, settings: Settings) -> QdrantConfig:
        return cls(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            source_collection=settings.qdrant_source_collection,
            skill_collection=settings.qdrant_skill_collection,
            dimensions=settings.embedding_dimensions,
        )


class QdrantVectorStore:
    def __init__(self, config: QdrantConfig):
        self.config = config
        self._client = None

    async def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as error:
            raise QdrantUnavailable("install qdrant-client to use Qdrant retrieval") from error
        self._client = AsyncQdrantClient(url=self.config.url, api_key=self.config.api_key)
        return self._client

    async def ensure_collections(self) -> None:
        try:
            from qdrant_client import models

            client = await self._get_client()
            for name in (self.config.source_collection, self.config.skill_collection):
                if not await client.collection_exists(name):
                    await client.create_collection(
                        collection_name=name,
                        vectors_config=models.VectorParams(
                            size=self.config.dimensions, distance=models.Distance.COSINE
                        ),
                    )
        except Exception as error:  # normalise the optional remote dependency
            if isinstance(error, QdrantUnavailable):
                raise
            raise QdrantUnavailable(f"Qdrant unavailable: {error}") from error

    async def upsert_source_chunks(self, points: list[dict[str, Any]]) -> None:
        await self._upsert(self.config.source_collection, points)

    async def upsert_skill_versions(self, points: list[dict[str, Any]]) -> None:
        await self._upsert(self.config.skill_collection, points)

    async def reconcile_collection(self, collection: str, points: list[dict[str, Any]]) -> None:
        """Upsert authoritative PostgreSQL points and remove stale index rows."""
        try:
            from qdrant_client import models

            await self._upsert(collection, points)
            client = await self._get_client()
            wanted = {str(point["id"]) for point in points}
            stale: list[str] = []
            offset = None
            while True:
                batch, offset = await client.scroll(
                    collection_name=collection,
                    limit=256,
                    offset=offset,
                    with_payload=False,
                    with_vectors=False,
                )
                stale.extend(str(point.id) for point in batch if str(point.id) not in wanted)
                if offset is None:
                    break
            if stale:
                await client.delete(
                    collection_name=collection,
                    points_selector=models.PointIdsList(points=stale),
                    wait=True,
                )
        except Exception as error:
            if isinstance(error, QdrantUnavailable):
                raise
            raise QdrantUnavailable(f"Qdrant reconciliation failed: {error}") from error

    async def _upsert(self, collection: str, points: list[dict[str, Any]]) -> None:
        if not points:
            return
        try:
            from qdrant_client import models

            await self.ensure_collections()
            client = await self._get_client()
            await client.upsert(
                collection_name=collection,
                wait=True,
                points=[
                    models.PointStruct(
                        id=point["id"], vector=point["vector"], payload=point.get("payload", {})
                    )
                    for point in points
                ],
            )
        except Exception as error:
            if isinstance(error, QdrantUnavailable):
                raise
            raise QdrantUnavailable(f"Qdrant upsert failed: {error}") from error

    async def query_sources(
        self, vector: list[float], *, filters: dict[str, Any] | None = None, limit: int = 20
    ) -> list[tuple[str, float, dict[str, Any]]]:
        return await self._query(
            self.config.source_collection, vector, filters=filters, limit=limit
        )

    async def query_skills(
        self, vector: list[float], *, limit: int = 8
    ) -> list[tuple[str, float, dict[str, Any]]]:
        return await self._query(self.config.skill_collection, vector, filters=None, limit=limit)

    async def _query(
        self,
        collection: str,
        vector: list[float],
        *,
        filters: dict[str, Any] | None,
        limit: int,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        try:
            from qdrant_client import models

            client = await self._get_client()
            must = [
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
                for key, value in (filters or {}).items()
                if value is not None
            ]
            response = await client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=models.Filter(must=must) if must else None,
                limit=limit,
            )
            return [
                (str(point.id), float(point.score), dict(point.payload or {}))
                for point in response.points
            ]
        except Exception as error:
            if isinstance(error, QdrantUnavailable):
                raise
            raise QdrantUnavailable(f"Qdrant query failed: {error}") from error
