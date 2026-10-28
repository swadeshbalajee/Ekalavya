"""Bodhan AI translation and text-to-speech for Indian languages.

API reference: https://console.bodhan.ai/api-docs/. Translation is
``POST /translate`` with ``{text, target_language}`` and reports ``truncated``.
Speech is ``POST /v1/audio/speech`` with model ``indic-speak``, a voice name and
``instructions`` carrying the language; it returns 24 kHz mono 16-bit WAV and
expects short inputs (about 30 seconds of speech), so narration is spoken in
sentence-sized chunks and joined with natural pauses.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.media.speech import to_speakable
from scholarmotion.observability.usage import record_service_usage
from scholarmotion.schemas import TTSResult

DEFAULT_BASE_URL = "https://api.bodhan.ai"
TTS_MODEL = "indic-speak"
SAMPLE_RATE = 24_000
# Our supported languages that Bodhan also covers. The rest (Arabic, French,
# Japanese, Spanish) stay on the OpenRouter providers.
LANGUAGE_CODES = {
    "bengali": "bn",
    "english": "en",
    "hindi": "hi",
    "marathi": "mr",
    "tamil": "ta",
    "telugu": "te",
}
# Speech chunks: one or two sentences, well under the ~30 s request limit.
MAX_CHUNK_CHARS = 220
SENTENCE_PAUSE_SECONDS = 0.18
PARAGRAPH_PAUSE_SECONDS = 0.8
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Published prices (INR): translation ₹0.20 per 10,000 output tokens; speech
# ₹6 per 10,000 input characters. Converted to dollars with ``usd_per_inr``.
TRANSLATE_INR_PER_OUTPUT_TOKEN = 0.20 / 10_000
SPEAK_INR_PER_CHARACTER = 6 / 10_000


def bodhan_language(language: str | None) -> str | None:
    """Bodhan's code for one of our language names, or None if it is not covered."""
    return LANGUAGE_CODES.get((language or "").strip().lower())


class BodhanError(RuntimeError):
    """A Bodhan request failed; callers fall back to the OpenRouter provider."""


async def _post(client: httpx.AsyncClient, url: str, api_key: str, payload: dict) -> httpx.Response:
    """POST with up to three attempts on rate limits and server errors."""
    for attempt in range(3):
        try:
            response = await client.post(
                url, headers={"Authorization": f"Bearer {api_key}"}, json=payload
            )
        except httpx.HTTPError as error:
            if attempt == 2:
                raise BodhanError(f"request failed: {error}") from error
        else:
            if response.status_code < 400:
                return response
            if response.status_code not in _RETRY_STATUS or attempt == 2:
                try:
                    detail = response.json().get("error", {})
                except ValueError:
                    detail = {}
                raise BodhanError(
                    f"HTTP {response.status_code} {detail.get('code', '')}: "
                    f"{detail.get('message', response.text[:200])}"
                )
        await asyncio.sleep(1.5 * (attempt + 1))
    raise BodhanError("request failed")


@dataclass
class BodhanTranslation:
    text: str
    truncated: bool
    output_tokens: int


class BodhanTranslator:
    """Plain-text machine translation from English into a supported language."""

    name = "bodhan"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        usd_per_inr: float = 0.012,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.usd_per_inr = usd_per_inr
        self.timeout = timeout
        self.transport = transport

    def supports(self, language: str | None) -> bool:
        return bodhan_language(language) is not None

    async def translate(self, text: str, target_language: str) -> BodhanTranslation:
        code = bodhan_language(target_language)
        if code is None:
            raise BodhanError(f"{target_language} is not supported by Bodhan")
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await _post(
                client,
                f"{self.base_url}/translate",
                self.api_key,
                {"text": text, "target_language": code},
            )
        body = response.json()
        usage = body.get("usage") or {}
        output_tokens = int(usage.get("completion_tokens") or 0)
        _record_usage(
            "translation",
            "bodhan/indic-translate",
            usage,
            characters=len(text),
            cost_usd=output_tokens * TRANSLATE_INR_PER_OUTPUT_TOKEN * self.usd_per_inr,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return BodhanTranslation(
            text=str(body.get("translation", "")).strip(),
            truncated=bool(body.get("truncated")),
            output_tokens=output_tokens,
        )


def _chunks(paragraph: str) -> list[str]:
    """Sentence-sized pieces of one paragraph, each at most MAX_CHUNK_CHARS."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?।॥])\s+", paragraph) if part.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > MAX_CHUNK_CHARS:
            cut = sentence.rfind(" ", 0, MAX_CHUNK_CHARS)
            cut = cut if cut > MAX_CHUNK_CHARS // 2 else MAX_CHUNK_CHARS
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if chunks and len(chunks[-1]) + len(sentence) + 1 <= MAX_CHUNK_CHARS:
            chunks[-1] = f"{chunks[-1]} {sentence}"
        elif sentence:
            chunks.append(sentence)
    return chunks


def _pcm_frames(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise BodhanError("unexpected audio format from Bodhan TTS")
        if audio.getframerate() != SAMPLE_RATE:
            raise BodhanError(f"unexpected sample rate {audio.getframerate()} from Bodhan TTS")
        return audio.readframes(audio.getnframes())


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


class BodhanTTSProvider:
    """Narration with Bodhan's indic-speak voices, one fixed voice per language."""

    name = "bodhan"
    model = TTS_MODEL

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "Kavya",
        voices: dict[str, str] | None = None,
        style: str = "educational lecture",
        base_url: str = DEFAULT_BASE_URL,
        usd_per_inr: float = 0.012,
        concurrency: int = 4,
        timeout: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.voice = voice
        self.voices = {key.lower(): value for key, value in (voices or {}).items()}
        self.style = style
        self.base_url = base_url.rstrip("/")
        self.usd_per_inr = usd_per_inr
        self.concurrency = concurrency
        self.timeout = timeout
        self.transport = transport

    def supports(self, language: str | None) -> bool:
        return bodhan_language(language) is not None

    def voice_for(self, language: str) -> str:
        code = bodhan_language(language) or ""
        return self.voices.get(code) or self.voices.get(language.lower()) or self.voice

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        code = bodhan_language(language)
        if code is None:
            raise BodhanError(f"{language} is not supported by Bodhan TTS")
        voice = self.voice_for(language)
        instructions = json.dumps({"lang": code, **({"style": self.style} if self.style else {})})
        paragraphs = [to_speakable(part) for part in re.split(r"\n\s*\n", text)]
        plan = [_chunks(paragraph) for paragraph in paragraphs if paragraph.strip()]
        if not any(plan):
            raise BodhanError("nothing to speak")
        limit = asyncio.Semaphore(self.concurrency)
        started = time.perf_counter()

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:

            async def speak(chunk: str) -> bytes:
                async with limit:
                    response = await _post(
                        client,
                        f"{self.base_url}/v1/audio/speech",
                        self.api_key,
                        {
                            "model": TTS_MODEL,
                            "input": chunk,
                            "voice": voice,
                            "instructions": instructions,
                        },
                    )
                return _pcm_frames(response.content)

            audio = [await asyncio.gather(*(speak(chunk) for chunk in chunks)) for chunks in plan]
        frames = bytearray()
        for index, paragraph in enumerate(audio):
            if index:
                frames += _silence(PARAGRAPH_PAUSE_SECONDS)
            for position, piece in enumerate(paragraph):
                if position:
                    frames += _silence(SENTENCE_PAUSE_SECONDS)
                frames += piece
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(bytes(frames))
        spoken = "\n\n".join(" ".join(chunks) for chunks in plan)
        _record_usage(
            "tts",
            f"bodhan/{TTS_MODEL}",
            {},
            characters=len(spoken),
            cost_usd=len(spoken) * SPEAK_INR_PER_CHARACTER * self.usd_per_inr,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        duration = wav_duration(str(output_path))
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=estimate_sentence_timings(spoken, duration),
            word_timings=[],
            voice_id=voice,
            provider=self.name,
            model=TTS_MODEL,
        )


class LanguageRoutedTTS:
    """Bodhan for the languages it covers, the configured provider for the rest."""

    def __init__(self, bodhan: BodhanTTSProvider, fallback):
        self.bodhan = bodhan
        self.fallback = fallback
        self.name = fallback.name
        self.model = fallback.model

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        if self.bodhan.supports(language):
            return await self.bodhan.synthesize(text, output_path, language=language)
        return await self.fallback.synthesize(text, output_path, language=language)


def _record_usage(
    purpose: str,
    model: str,
    usage: dict,
    *,
    characters: int,
    cost_usd: float,
    latency_ms: float | None = None,
) -> None:
    """Add this call to the build's usage ledger, if one is open."""
    record_service_usage(
        purpose=purpose,
        model=model,
        cost_usd=round(cost_usd, 6),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        characters=characters,
        provider="bodhan",
        latency_ms=latency_ms,
    )


def parse_voice_map(value: str | None) -> dict[str, str]:
    """``"ta:Name,hi:Kavya"`` → ``{"ta": "Name", "hi": "Kavya"}``."""
    pairs = (item.split(":", 1) for item in (value or "").split(",") if ":" in item)
    return {key.strip().lower(): voice.strip() for key, voice in pairs if voice.strip()}
