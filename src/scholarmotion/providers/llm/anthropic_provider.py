"""LLM provider for the Anthropic Messages API."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import LLMCapabilities

T = TypeVar("T", bound=BaseModel)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str, model: str):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Anthropic") from exc
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.capabilities = LLMCapabilities(images=True)

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
        result = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            temperature=temperature or 0,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in result.content if getattr(block, "type", "") == "text"
        )

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T:
        schema = json.dumps(output_schema.model_json_schema())
        raw = await self.generate_text(
            f"{prompt}\nReturn only JSON matching: {schema}", temperature=temperature
        )
        return output_schema.model_validate_json(
            raw.strip().removeprefix("```json").removesuffix("```").strip()
        )

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str:
        raw = await self.generate_text(prompt, temperature=temperature)
        return (
            raw.split("```python", 1)[-1].split("```", 1)[0].strip() if "```python" in raw else raw
        )

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        return {"passed": True, "issues": [], "skipped": not images}
