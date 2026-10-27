"""Speech-to-text provider adapters."""

from .base import STTProvider
from .mock import MockSTTProvider
from .openai_compatible import OpenAICompatibleSTTProvider

__all__ = ["MockSTTProvider", "OpenAICompatibleSTTProvider", "STTProvider"]
