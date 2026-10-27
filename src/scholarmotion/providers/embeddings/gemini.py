from __future__ import annotations

import math


class GeminiEmbeddingProvider:
    """Gemini text embeddings, truncated and normalized for Qdrant."""

    name = "gemini"

    def __init__(self, *, api_key: str, model: str, dimensions: int = 384):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Gemini embeddings") from exc
        if not 128 <= dimensions <= 3072:
            raise ValueError("Gemini embedding dimensions must be between 128 and 3072")
        self.client = genai.Client(api_key=api_key)
        self.types = types
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        # The GenerateContent embedding endpoint accepts individual Content
        # objects in one call. Keep batches bounded for source-heavy projects.
        for start in range(0, len(texts), 100):
            batch = texts[start : start + 100]
            contents = [self.types.Content(parts=[self.types.Part(text=text)]) for text in batch]
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=contents,
                config=self.types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY",
                    output_dimensionality=self.dimensions,
                ),
            )
            for item in response.embeddings or []:
                values = list(item.values or [])
                norm = math.sqrt(sum(value * value for value in values)) or 1.0
                vectors.append([value / norm for value in values])
        return vectors
