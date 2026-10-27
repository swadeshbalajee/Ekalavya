"""LLM provider protocol and capability flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


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
