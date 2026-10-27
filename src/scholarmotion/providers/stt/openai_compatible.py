"""Speech-to-text with an OpenAI-compatible transcription endpoint."""

from __future__ import annotations

from pathlib import Path

import httpx

from scholarmotion.schemas import STTResult

_LANGUAGE_CODES = {
    "arabic": "ar",
    "bengali": "bn",
    "english": "en",
    "french": "fr",
    "hindi": "hi",
    "japanese": "ja",
    "marathi": "mr",
    "spanish": "es",
    "tamil": "ta",
    "telugu": "te",
}


def _language_code(language: str | None) -> str | None:
    """Convert the UI's language label into the short code expected by STT APIs."""
    if not language:
        return None
    value = language.strip()
    return _LANGUAGE_CODES.get(value.casefold(), value if len(value) in {2, 5} else None)


class OpenAICompatibleSTTProvider:
    name = "openai_compatible"

    def __init__(self, *, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> STTResult:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {"model": self.model}
        if code := _language_code(language):
            data["language"] = code
        async with httpx.AsyncClient(timeout=120) as client:
            with audio_path.open("rb") as audio:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": (audio_path.name, audio, "audio/wav")},
                )
        response.raise_for_status()
        payload = response.json()
        return STTResult(text=payload.get("text", ""), language=language)
