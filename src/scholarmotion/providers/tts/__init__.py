"""Text-to-speech provider adapters."""

from .base import TTSProvider
from .elevenlabs import ElevenLabsTTSProvider
from .kokoro_http import KokoroHTTPTTSProvider
from .mock import MockTTSProvider
from .openai_compatible import OpenAICompatibleTTSProvider

__all__ = [
    "ElevenLabsTTSProvider",
    "KokoroHTTPTTSProvider",
    "MockTTSProvider",
    "OpenAICompatibleTTSProvider",
    "TTSProvider",
]
