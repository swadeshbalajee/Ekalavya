"""Measure when each narration word is spoken (CTC forced alignment).

The TTS providers return audio without timestamps, and word-share estimates
drift by a second or more once speech contains spoken math or pauses. Since
the exact script is known, a small multilingual acoustic model (MMS, run
locally on CPU) can place every word in the recording. Non-Latin scripts are
romanized with uroman first, which is how the MMS aligner was trained.

Every failure returns None so callers keep the estimated timings.
"""

from __future__ import annotations

import logging
import re
import threading
import wave
from functools import lru_cache

import numpy as np

from scholarmotion.schemas import TimingCue

logger = logging.getLogger(__name__)

DEFAULT_ALIGNER_MODEL = "MahmoudAshraf/mms-300m-1130-forced-aligner"
_SAMPLE_RATE = 16_000
# wav2vec2 attention grows with input length; long clips are scored in windows.
_WINDOW_SECONDS = 30
_lock = threading.Lock()


@lru_cache(maxsize=2)
def _load(model_id: str):
    from transformers import AutoModelForCTC, AutoProcessor

    try:  # the weights are cached after the first download; skip the Hub check
        processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
        model = AutoModelForCTC.from_pretrained(model_id, local_files_only=True)
    except OSError:
        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForCTC.from_pretrained(model_id)
    model.eval()
    return processor, model


@lru_cache(maxsize=1)
def _romanizer():
    import uroman

    return uroman.Uroman()


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {8 * width}-bit")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != _SAMPLE_RATE:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(rate, _SAMPLE_RATE)
        samples = resample_poly(samples, _SAMPLE_RATE // divisor, rate // divisor)
    return samples.astype(np.float32)


def _emissions(samples: np.ndarray, model_id: str) -> np.ndarray:
    import torch

    processor, model = _load(model_id)
    window = _WINDOW_SECONDS * _SAMPLE_RATE
    chunks = []
    with torch.inference_mode():
        for start in range(0, len(samples), window):
            piece = samples[start : start + window]
            if len(piece) < _SAMPLE_RATE // 10:
                continue
            inputs = processor(piece, sampling_rate=_SAMPLE_RATE, return_tensors="pt")
            logits = model(inputs.input_values).logits[0]
            chunks.append(torch.log_softmax(logits, dim=-1).numpy())
    return np.concatenate(chunks)


def _word_tokens(words: list[str], vocab: dict[str, int]) -> list[list[int]]:
    """Character ids per word; empty for words with nothing speakable (e.g. '—')."""
    roman = _romanizer()
    tokens = []
    for word in words:
        text = word if word.isascii() else roman.romanize_string(word)
        letters = re.sub(r"[^a-z']", "", text.casefold())
        tokens.append([vocab[char] for char in letters if char in vocab])
    return tokens


def _viterbi(emissions: np.ndarray, targets: list[int], blank: int) -> np.ndarray | None:
    """Best CTC path; returns the target index each frame emits (-1 for blank)."""
    states = [blank]
    for token in targets:
        states += [token, blank]
    count, frames = len(states), len(emissions)
    if frames < len(targets):
        return None
    states = np.array(states)
    skip = np.zeros(count, dtype=bool)
    skip[2:] = (states[2:] != blank) & (states[2:] != states[:-2])
    score = np.full(count, -np.inf)
    score[0], score[1] = emissions[0, blank], emissions[0, states[1]]
    back = np.zeros((frames, count), dtype=np.int8)
    for frame in range(1, frames):
        stay = score
        step = np.concatenate(([-np.inf], score[:-1]))
        jump = np.where(skip, np.concatenate(([-np.inf, -np.inf], score[:-2])), -np.inf)
        options = np.stack([stay, step, jump])
        choice = options.argmax(axis=0)
        score = options[choice, np.arange(count)] + emissions[frame, states]
        back[frame] = choice
    state = count - 1 if score[-1] >= score[-2] else count - 2
    if not np.isfinite(score[state]):
        return None
    path = np.empty(frames, dtype=np.int64)
    for frame in range(frames - 1, -1, -1):
        path[frame] = state
        state -= int(back[frame, state])
    # Odd states are targets: state 2k+1 emits target k.
    return np.where(path % 2 == 1, path // 2, -1)


def align_words(
    audio_path: str,
    text: str,
    *,
    model_id: str = DEFAULT_ALIGNER_MODEL,
) -> list[TimingCue] | None:
    """One cue per whitespace-separated word of ``text``, or None on failure."""
    words = text.split()
    if not words:
        return None
    try:
        with _lock:
            processor, _ = _load(model_id)
            vocab = processor.tokenizer.get_vocab()
            samples = _read_wav(audio_path)
            emissions = _emissions(samples, model_id)
        duration = len(samples) / _SAMPLE_RATE
        per_word = _word_tokens(words, vocab)
        targets = [token for tokens in per_word for token in tokens]
        if not targets:
            return None
        path = _viterbi(emissions, targets, vocab["<blank>"])
    except Exception as error:  # noqa: BLE001 - alignment is an optional refinement
        logger.warning("Narration alignment failed for %s: %s", audio_path, error)
        return None
    if path is None:
        logger.warning("Narration alignment found no path for %s", audio_path)
        return None
    seconds = duration / len(emissions)
    first = np.full(len(targets), -1)
    last = np.full(len(targets), -1)
    for frame, target in enumerate(path):
        if target >= 0:
            if first[target] < 0:
                first[target] = frame
            last[target] = frame
    spans: list[tuple[float, float] | None] = []
    cursor = 0
    for tokens in per_word:
        if not tokens:
            spans.append(None)
            continue
        begin, end = first[cursor], last[cursor + len(tokens) - 1]
        cursor += len(tokens)
        spans.append(None if begin < 0 or end < 0 else (begin * seconds, (end + 1) * seconds))
    return _fill_gaps(words, spans, duration)


def _fill_gaps(
    words: list[str], spans: list[tuple[float, float] | None], duration: float
) -> list[TimingCue] | None:
    """Give unalignable words (symbols, dashes) the gap between their neighbours."""
    if all(span is None for span in spans):
        return None
    cues = []
    for index, (word, span) in enumerate(zip(words, spans, strict=True)):
        if span is None:
            before = next((s[1] for s in reversed(spans[:index]) if s), 0.0)
            after = next((s[0] for s in spans[index + 1 :] if s), duration)
            span = (before, max(before + 0.01, after))
        start = max(0.0, min(span[0], duration - 0.01))
        cues.append(
            TimingCue(text=word, start=start, end=max(start + 0.01, min(span[1], duration)))
        )
    return cues


def sentence_timings_from_words(
    text: str, words: list[TimingCue], duration: float
) -> list[TimingCue]:
    """Sentence cues that start when their first word is spoken.

    Each cue runs until the next sentence begins, so subtitles and animation
    cues stay on screen through the natural pause after a sentence.
    """
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    sentences = sentences or [text]
    starts, cursor = [], 0
    for sentence in sentences:
        starts.append(0.0 if cursor == 0 else words[min(cursor, len(words) - 1)].start)
        cursor += len(sentence.split())
    ends = [*starts[1:], duration]
    return [
        TimingCue(text=sentence, start=start, end=max(start + 0.001, end))
        for sentence, start, end in zip(sentences, starts, ends, strict=True)
    ]


def scene_cuts(words: list[TimingCue], word_counts: list[int]) -> list[float] | None:
    """Cut a lesson take in the pause between each scene's last and next first word."""
    if sum(word_counts) != len(words) or any(count <= 0 for count in word_counts):
        return None
    cuts, index = [], 0
    for count in word_counts[:-1]:
        index += count
        last_end, next_start = words[index - 1].end, words[index].start
        cuts.append((last_end + next_start) / 2 if next_start > last_end else next_start)
    if any(later <= earlier for earlier, later in zip([0.0, *cuts], cuts, strict=False)):
        return None
    return cuts


def words_between(words: list[TimingCue], start: float, duration: float) -> list[TimingCue]:
    """Re-base a scene's words on its own clip, which begins at ``start``."""
    shifted = []
    for word in words:
        begin = min(max(0.0, word.start - start), max(0.0, duration - 0.01))
        end = max(begin + 0.01, min(word.end - start, duration))
        shifted.append(TimingCue(text=word.text, start=begin, end=end))
    return shifted
