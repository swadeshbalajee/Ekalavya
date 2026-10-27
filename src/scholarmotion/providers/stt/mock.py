from __future__ import annotations

import wave
from pathlib import Path

from scholarmotion.schemas import STTResult


class MockSTTProvider:
    """Offline test provider. It reports container timing but never invents speech."""

    name = "mock"

    def __init__(self, model: str = "deterministic-v1"):
        self.model = model

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> STTResult:
        duration: float | None = None
        try:
            with wave.open(str(audio_path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
        except (wave.Error, OSError):
            pass
        return STTResult(text="", language=language, duration_seconds=duration, confidence=0.0)
