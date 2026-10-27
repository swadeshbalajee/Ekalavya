"""Embedding provider adapters."""

from .base import EmbeddingProvider
from .local import LocalEmbeddingProvider
from .openai_compatible import OpenAICompatibleEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
]
