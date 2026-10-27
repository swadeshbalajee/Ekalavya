"""Text-to-speech with an OpenAI-compatible speech endpoint."""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import wave
from pathlib import Path

import httpx

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.media.speech import to_speakable
from scholarmotion.schemas import TTSResult


class OpenAICompatibleTTSProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        voice: str | None = "alloy",
        ffmpeg_binary: str = "ffmpeg",
        instructions: str = "",
        response_format: str = "mp3",
        pcm_sample_rate: int = 24000,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.instructions = instructions
        self.response_format = response_format
        self.pcm_sample_rate = pcm_sample_rate
        self.ffmpeg_binary = shutil.which(ffmpeg_binary) or str(
            Path(sys.prefix) / "bin" / ffmpeg_binary
        )

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        # Keep paragraph breaks: a whole-lesson take uses them as scene pauses.
        paragraphs = [to_speakable(part) for part in re.split(r"\n\s*\n", text)]
        text = "\n\n".join(part for part in paragraphs if part)
        async with httpx.AsyncClient(timeout=300) as client:
            payload = {
                "model": self.model,
                "input": text,
                # Gemini TTS returns raw PCM; other models may return MP3.
                # Both paths produce a real WAV for assembly and duration reads.
                "response_format": self.response_format,
            }
            if self.voice:
                payload["voice"] = self.voice
            if self.instructions:
                payload["instructions"] = (
                    self.instructions
                    + f" Narration language: {language}."
                    + (" Pause for about one second between paragraphs." if "\n\n" in text else "")
                )
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.response_format == "pcm":
            if len(response.content) < 2 or len(response.content) % 2:
                raise RuntimeError("TTS returned invalid 16-bit PCM audio")
            with wave.open(str(output_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(self.pcm_sample_rate)
                audio.writeframes(response.content)
            return TTSResult(
                audio_path=str(output_path),
                duration_seconds=wav_duration(str(output_path)),
                sentence_timings=estimate_sentence_timings(text, wav_duration(str(output_path))),
                word_timings=[],
                voice_id=self.voice,
                provider=self.name,
                model=self.model,
            )
        source_path = output_path.with_suffix(".source.mp3")
        source_path.write_bytes(response.content)
        try:
            process = await asyncio.create_subprocess_exec(
                self.ffmpeg_binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(
                    "TTS audio conversion failed: "
                    + (
                        stderr.decode(errors="replace").strip()
                        or f"ffmpeg exited with {process.returncode}"
                    )
                )
        finally:
            source_path.unlink(missing_ok=True)
        duration = wav_duration(str(output_path))
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=estimate_sentence_timings(text, duration),
            word_timings=[],
            voice_id=self.voice,
            provider=self.name,
            model=self.model,
        )
