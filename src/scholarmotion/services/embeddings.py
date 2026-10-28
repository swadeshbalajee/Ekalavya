"""Embeddings for ranking and graph search, computed off the event loop."""

from __future__ import annotations

import asyncio
import logging

from scholarmotion.config import Settings
from scholarmotion.providers.factory import create_embedding_provider

logger = logging.getLogger(__name__)


_PROVIDERS: dict[int, object] = {}


def _provider(settings: Settings):
    # Settings are not hashable; one provider (one loaded model) per settings object.
    if id(settings) not in _PROVIDERS:
        _PROVIDERS[id(settings)] = create_embedding_provider(settings)
    return _PROVIDERS[id(settings)]


async def embed_texts(settings: Settings, texts: list[str]) -> list[list[float]] | None:
    """Vectors for ``texts``, or None when no embedding model is available."""
    if not texts:
        return []
    try:
        # Loading and running a local model is CPU work; keep it off the event loop.
        provider = await asyncio.to_thread(_provider, settings)
        model = getattr(provider, "_model", None)
        if model is not None:
            return await asyncio.to_thread(
                lambda: model.encode(texts, normalize_embeddings=True).tolist()
            )
        return await provider.embed(texts)
    except Exception as error:  # noqa: BLE001 - callers fall back to non-semantic ranking
        logger.warning("Embeddings unavailable: %s", error)
        return None
