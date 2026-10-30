"""LLM provider protocol and capability flags."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_CALL_TIMEOUT: ContextVar[float | None] = ContextVar("llm_call_timeout", default=None)


@contextmanager
def call_timeout(seconds: float) -> Iterator[None]:
    """Let each model request inside this block run up to ``seconds``.

    For stages whose answers are long (a whole lesson script), where the
    default per-request limit would cut a healthy model off mid-answer.
    """
    token = _CALL_TIMEOUT.set(seconds)
    try:
        yield
    finally:
        _CALL_TIMEOUT.reset(token)


def call_timeout_seconds(default: float) -> float:
    """The per-request limit: the longer of ``default`` and the block's limit."""
    return max(default, _CALL_TIMEOUT.get() or 0)


@dataclass(frozen=True)
class LLMCapabilities:
    structured_output: bool = True
    code: bool = True
    images: bool = False
    video_frames: bool = False


class LLMProvider(Protocol):
    name: str
    model: str
    capabilities: LLMCapabilities

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T: ...

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str: ...

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str: ...

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]: ...
