"""Speech-to-text provider protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from scholarmotion.schemas import STTResult


class STTProvider(Protocol):
    name: str
    model: str

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> STTResult: ...
