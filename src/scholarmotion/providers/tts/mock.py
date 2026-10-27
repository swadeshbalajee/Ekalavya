"""Deterministic placeholder TTS sized to the text, for tests and offline runs."""

from __future__ import annotations

import re
import wave
from pathlib import Path

from scholarmotion.schemas import TimingCue, TTSResult


class MockTTSProvider:
    name = "mock"

    def __init__(self, model: str = "deterministic-v1", words_per_minute: float = 150):
        self.model = model
        self.words_per_minute = words_per_minute

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        words = re.findall(r"\S+", text)
        duration = max(0.4, len(words) * 60 / self.words_per_minute)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rate = 16_000
        frames = int(duration * rate)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b"\x00\x00" * frames)
        word_duration = duration / max(1, len(words))
        word_timings = [
            TimingCue(text=word, start=i * word_duration, end=(i + 1) * word_duration)
            for i, word in enumerate(words)
        ]
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        sentence_timings: list[TimingCue] = []
        cursor = 0.0
        for sentence in sentences or [text]:
            seconds = max(0.1, len(re.findall(r"\S+", sentence)) * 60 / self.words_per_minute)
            sentence_timings.append(
                TimingCue(text=sentence, start=cursor, end=min(duration, cursor + seconds))
            )
            cursor += seconds
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=sentence_timings,
            word_timings=word_timings,
        )
