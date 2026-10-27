"""Text-to-speech provider protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from scholarmotion.schemas import TTSResult


class TTSProvider(Protocol):
    name: str
    model: str

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult: ...
