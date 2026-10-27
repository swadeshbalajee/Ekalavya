from __future__ import annotations

import re
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import LLMCapabilities

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider:
    """Deterministic provider for demos and tests; no external services required."""

    name = "mock"
    capabilities = LLMCapabilities(structured_output=True, code=True, images=False)

    def __init__(self, model: str = "deterministic-v1"):
        self.model = model

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T:
        marker = re.search(r"<OUTPUT_JSON>(.*?)</OUTPUT_JSON>", prompt, re.DOTALL)
        if marker:
            return output_schema.model_validate_json(marker.group(1))
        fields: dict[str, Any] = {}
        for name, field in output_schema.model_fields.items():
            if not field.is_required():
                fields[name] = field.get_default(call_default_factory=True)
        return output_schema.model_validate(fields)

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
        return prompt.split("TASK:", 1)[-1].strip() if "TASK:" in prompt else prompt.strip()

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str:
        class_match = re.search(
            r"Scene subclass MUST be named exactly `([A-Za-z_][A-Za-z0-9_]*)`", prompt
        )
        scene_class = class_match.group(1) if class_match else "SceneS01"
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', prompt)
        title = (title_match.group(1) if title_match else scene_class).replace('"', "'")
        return (
            "from manim import *\n"
            "from scholarmotion.manim_runtime.components import SafeTitle, SubtitleSafeRegion\n\n"
            f"class {scene_class}(Scene):\n"
            "    def construct(self):\n"
            f'        title = SafeTitle("{title}")\n'
            "        safe = SubtitleSafeRegion()\n"
            "        self.add(safe)\n"
            "        self.play(Write(title), run_time=0.8)\n"
            "        self.wait(0.2)\n"
        )

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        return {"passed": True, "issues": [], "reason": "mock provider has no vision capability"}
