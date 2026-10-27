"""Text-to-speech with the ElevenLabs API."""

from __future__ import annotations

import wave
from pathlib import Path

import httpx

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.schemas import TTSResult

PCM_SAMPLE_RATE = 16000


class ElevenLabsTTSProvider:
    name = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "eleven_flash_v2_5",
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
    ):
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                params={"output_format": f"pcm_{PCM_SAMPLE_RATE}"},
                json={"text": text, "model_id": self.model},
            )
            response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(PCM_SAMPLE_RATE)
            wav.writeframes(response.content)
        duration = wav_duration(output_path)
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=estimate_sentence_timings(text, duration),
            word_timings=[],
        )
