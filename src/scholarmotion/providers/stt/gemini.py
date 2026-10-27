"""Speech-to-text with the Gemini API."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from scholarmotion.schemas import STTResult


def _language_code(language: str | None) -> str | None:
    if not language:
        return None
    value = language.strip()
    known = {
        "arabic": "ar-SA",
        "bengali": "bn-IN",
        "english": "en-US",
        "french": "fr-FR",
        "hindi": "hi-IN",
        "japanese": "ja-JP",
        "marathi": "mr-IN",
        "spanish": "es-ES",
        "tamil": "ta-IN",
        "telugu": "te-IN",
    }
    return known.get(value.casefold(), value if len(value) in {2, 5} else None)


class GeminiSTTProvider:
    """Speech-to-text using Gemini's task-specific transcription model."""

    name = "gemini"

    def __init__(self, *, api_key: str, model: str):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Gemini STT") from exc
        self.client = genai.Client(api_key=api_key)
        self.types = types
        self.model = model

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> STTResult:
        content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
        audio = self.types.Part.from_bytes(data=audio_path.read_bytes(), mime_type=content_type)
        code = _language_code(language)
        config = self.types.GenerateContentConfig(
            audio_transcription_config=self.types.AudioTranscriptionConfig(
                language_codes=[code] if code else []
            )
        )
        response = None
        for attempt in range(4):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model, contents=[audio], config=config
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                message = str(error).upper()
                if attempt == 3 or not any(
                    marker in message
                    for marker in ("429", "500", "502", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
                ):
                    raise
                await asyncio.sleep(min(2**attempt, 15))
        return STTResult(text=response.text or "", language=language)
