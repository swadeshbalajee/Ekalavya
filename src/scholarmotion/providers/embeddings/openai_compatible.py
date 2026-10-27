"""Embeddings from any OpenAI-compatible /v1/embeddings endpoint."""

from __future__ import annotations

import httpx


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        dimensions: int = 384,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts, "dimensions": self.dimensions},
            )
            response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda item: item["index"])
        if rows:
            self.dimensions = len(rows[0]["embedding"])
        return [row["embedding"] for row in rows]
