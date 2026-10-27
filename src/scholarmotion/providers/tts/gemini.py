"""Text-to-speech with the Gemini API."""

from __future__ import annotations

import asyncio
import re
import wave
from pathlib import Path

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.media.speech import to_speakable
from scholarmotion.schemas import TTSResult


def _language_code(language: str) -> str | None:
    value = language.strip().casefold()
    known = {
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
    return known.get(value, value if re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language) else None)


class GeminiTTSProvider:
    """Text-to-speech through Gemini's native audio generation API."""

    name = "gemini"

    def __init__(self, *, api_key: str, model: str, voice: str = "Kore"):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Gemini TTS") from exc
        self.client = genai.Client(api_key=api_key)
        self.types = types
        self.model = model
        self.voice = voice or "Kore"

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        paragraphs = [to_speakable(part) for part in re.split(r"\n\s*\n", text)]
        text = "\n\n".join(part for part in paragraphs if part)
        language_code = _language_code(language)
        config = self.types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=self.types.SpeechConfig(
                language_code=language_code,
                voice_config=self.types.VoiceConfig(
                    prebuilt_voice_config=self.types.PrebuiltVoiceConfig(voice_name=self.voice)
                ),
            ),
        )
        # Build SDK Content/Part objects instead of raw dictionaries. Extra fields
        # such as speech_metadata are not accepted by GenerateContentParameters
        # and caused the full video workflow to fail before reaching the API.
        contents = self.types.Content(
            role="user",
            parts=[
                self.types.Part(
                    text=(
                        f"Read the following narration verbatim in {language}. Use a warm, clear, "
                        "conversational STEM tutor's voice with an unhurried, steady pace and the "
                        "same energy throughout. Pause briefly between paragraphs and let the "
                        "final sentence settle.\n\n" + text
                    )
                )
            ],
        )
        response = None
        for attempt in range(4):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config
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

        audio = next(
            (
                part.inline_data.data
                for candidate in (response.candidates or [])
                for part in (candidate.content.parts or [])
                if getattr(part, "inline_data", None) and getattr(part.inline_data, "data", None)
            ),
            None,
        )
        if not audio:
            raise RuntimeError("Gemini TTS returned no audio data")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if audio[:4] == b"RIFF":
            output_path.write_bytes(audio)
        else:
            # Gemini's non-streaming endpoint normally returns a WAV container;
            # wrap raw 24 kHz mono PCM if the service returns linear audio.
            with wave.open(str(output_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(audio)
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
