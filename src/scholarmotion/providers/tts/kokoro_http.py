from __future__ import annotations

from pathlib import Path

import httpx

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.schemas import TTSResult


class KokoroHTTPTTSProvider:
    """Client for a self-hosted Kokoro-82M server (see scripts/kokoro_server.py)."""

    name = "kokoro_http"

    def __init__(self, *, base_url: str, voice: str = "af_heart", lang_code: str = "a"):
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.lang_code = lang_code

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        # CPU synthesis of a long scene can take several minutes.  A single
        # self-hosted Kokoro instance should not be treated like a low-latency
        # cloud TTS API.
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.post(
                f"{self.base_url}/synthesize",
                json={"text": text, "voice": self.voice, "lang_code": self.lang_code},
            )
            response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        duration = wav_duration(output_path)
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=estimate_sentence_timings(text, duration),
            word_timings=[],
        )
