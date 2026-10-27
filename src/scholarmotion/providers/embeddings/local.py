from __future__ import annotations

import hashlib
import math
import re


class LocalEmbeddingProvider:
    """Sentence-transformers when available, deterministic hashed vectors otherwise."""

    dimensions = 384

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model
        self._model = None
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model)
            self.dimensions = int(self._model.get_sentence_embedding_dimension())
        except (ImportError, OSError):
            self._model = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is not None:
            return self._model.encode(texts, normalize_embeddings=True).tolist()
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dimensions
                vector[idx] += -1.0 if digest[4] & 1 else 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1
            vectors.append([value / norm for value in vector])
        return vectors
