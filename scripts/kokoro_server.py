"""Hosts Kokoro-82M (Apache-2.0, https://huggingface.co/hexgrad/Kokoro-82M) as a small
HTTP TTS service. Runs in the standalone `kokoro-tts` conda env (torch + kokoro + fastapi),
separate from the main Ekalavya environment, since it needs GPU-enabled torch.

Run with:
    uvicorn scripts.kokoro_server:app --host 0.0.0.0 --port 8811

Set KOKORO_DEVICE=cuda to explicitly use a working CUDA setup. CPU is the
safe default and avoids accidentally selecting an unusable GPU driver.
"""

from __future__ import annotations

import io
import os

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from kokoro import KPipeline
from pydantic import BaseModel

app = FastAPI(title="Kokoro TTS server")

_requested_device = os.getenv("KOKORO_DEVICE", "cpu").lower()
_device = "cuda" if _requested_device == "cuda" and torch.cuda.is_available() else "cpu"
_pipelines: dict[str, KPipeline] = {}


def _pipeline_for(lang_code: str) -> KPipeline:
    if lang_code not in _pipelines:
        _pipelines[lang_code] = KPipeline(lang_code=lang_code, device=_device)
    return _pipelines[lang_code]


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    lang_code: str = "a"
    speed: float = 1.0


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": _device,
        "cuda_devices": torch.cuda.device_count() if _device == "cuda" else 0,
    }


@app.post("/synthesize")
def synthesize(req: SynthesizeRequest):
    if not req.text.strip():
        raise HTTPException(400, "text must not be empty")
    pipeline = _pipeline_for(req.lang_code)
    chunks = [audio for _, _, audio in pipeline(req.text, voice=req.voice, speed=req.speed)]
    if not chunks:
        raise HTTPException(500, "no audio produced")
    # Kokoro returns NumPy arrays on CPU and may return torch tensors on CUDA.
    # Normalize both forms before writing a WAV response.
    arrays = [
        chunk.detach().cpu().numpy() if isinstance(chunk, torch.Tensor) else np.asarray(chunk)
        for chunk in chunks
    ]
    audio = np.concatenate(arrays)
    buffer = io.BytesIO()
    sf.write(buffer, audio, 24000, format="WAV")
    return Response(content=buffer.getvalue(), media_type="audio/wav")
