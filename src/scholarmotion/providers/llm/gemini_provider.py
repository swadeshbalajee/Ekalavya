"""LLM and vision provider for the Gemini API."""

from __future__ import annotations

import asyncio
import logging
import re
import weakref
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from scholarmotion.observability import record_provider_event

from .base import LLMCapabilities, call_timeout_seconds, restore_latex_escapes

T = TypeVar("T", bound=BaseModel)

# Free/shared Gemini capacity is quota limited. Respect a retry hint when one
# is present and wait through a complete quota window otherwise.
MAX_RETRY_DELAY_SECONDS = 75.0
REQUEST_TIMEOUT_SECONDS = 60.0
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "429")
logger = logging.getLogger(__name__)
_REQUEST_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _request_semaphore() -> asyncio.Semaphore:
    """Share a conservative per-event-loop limit across Gemini capabilities."""
    loop = asyncio.get_running_loop()
    semaphore = _REQUEST_SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(1)
        _REQUEST_SEMAPHORES[loop] = semaphore
    return semaphore


def _retry_delay(error: Exception, attempt: int) -> float:
    text = str(error)
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", text)
    if match:
        return min(float(match.group(1)) + 1.0, MAX_RETRY_DELAY_SECONDS)
    if any(marker in text.upper() for marker in QUOTA_MARKERS):
        return min(60.0 * (attempt + 1), MAX_RETRY_DELAY_SECONDS)
    return float(2**attempt)


class _VisualIssue(BaseModel):
    category: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    frames: list[int] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    description: str
    suggested_repair: str
    correction_memory_candidate: bool = False


class _VisualReport(BaseModel):
    issues: list[_VisualIssue] = Field(default_factory=list)


class GeminiProvider:
    name = "gemini"
    frame_only_video_review = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback_model: str | None = None,
        default_temperature: float = 0.7,
    ):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Gemini") from exc
        self.client = genai.Client(api_key=api_key)
        self.types = types
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else None
        self.default_temperature = default_temperature
        self.capabilities = LLMCapabilities(images=True, video_frames=True)

    def _config(self, *, reasoning_effort: str | None = None, **values):
        if values.get("temperature") is None:
            values["temperature"] = getattr(self, "default_temperature", 0.7)
        # Gemini 3.x rejects legacy sampling parameters such as temperature.
        # Keep Flash calls economical unless an agent explicitly requests
        # deeper reasoning for pedagogical planning.
        if self.model.startswith("gemini-3"):
            values.pop("temperature", None)
            values.setdefault(
                "thinking_config",
                self.types.ThinkingConfig(thinking_level=reasoning_effort or "low"),
            )
        return self.types.GenerateContentConfig(**values)

    async def _generate(self, contents, config):
        retryable_markers = ("429", "500", "502", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
        active_model = self.model
        semaphore = _request_semaphore()
        for attempt in range(5):
            try:
                async with semaphore:
                    result = await asyncio.wait_for(
                        self.client.aio.models.generate_content(
                            model=active_model, contents=contents, config=config
                        ),
                        timeout=call_timeout_seconds(REQUEST_TIMEOUT_SECONDS),
                    )
                record_provider_event(provider=self.name, outcome="success")
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_text = str(exc).upper()
                retryable = any(marker in error_text for marker in retryable_markers)
                daily_quota = "PERDAYPERPROJECTPERMODEL" in error_text.replace("_", "")
                if active_model == self.model and self.fallback_model and retryable:
                    logger.warning(
                        "Gemini model %s returned a retryable error; falling back to %s",
                        active_model,
                        self.fallback_model,
                    )
                    active_model = self.fallback_model
                    record_provider_event(provider=self.name, outcome="fallback")
                    continue
                # A per-day free-tier quota cannot recover on a short retry
                # window. Fail promptly so callers can surface the error or
                # switch to another configured Gemini model.
                if daily_quota or attempt == 4 or not retryable:
                    record_provider_event(provider=self.name, outcome="error")
                    raise
                await asyncio.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("Gemini generation retry loop exited unexpectedly")

    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        result = await self._generate(
            prompt,
            self._config(temperature=temperature, reasoning_effort=reasoning_effort),
        )
        return result.text or ""

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T:
        result = await self._generate(
            prompt,
            self._config(
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=output_schema,
            ),
        )
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, output_schema):
            return restore_latex_escapes(parsed)
        if isinstance(parsed, dict):
            return restore_latex_escapes(output_schema.model_validate(parsed))
        raw = (result.text or "").strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        return restore_latex_escapes(output_schema.model_validate_json(raw))

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str:
        raw = await self.generate_text(prompt, temperature=temperature)
        return (
            raw.split("```python", 1)[-1].split("```", 1)[0].strip()
            if "```python" in raw
            else raw.strip()
        )

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        if not images:
            return {"passed": True, "skipped": True, "issues": []}
        content = [prompt]
        content.extend(
            self.types.Part.from_bytes(data=image, mime_type="image/png") for image in images
        )
        vision_prompt = (
            "Inspect every supplied frame against the scene description. Report only visible, "
            "actionable defects. Return an empty issues list when no defect is supported by "
            "the frames. Use category names beginning with visual.; use severity low, medium, "
            "high, or critical. Frame indices are zero-based and refer to the supplied image "
            "order. Include a concise repair suggestion for each issue.\n\n" + prompt
        )
        content[0] = vision_prompt
        result = await self._generate(
            content,
            self._config(
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=_VisualReport,
            ),
        )
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, _VisualReport):
            report = parsed
        else:
            raw = (result.text or "").strip().removeprefix("```json").removesuffix("```").strip()
            report = _VisualReport.model_validate_json(raw)
        issues = [item.model_dump() for item in report.issues]
        return {
            "passed": not any(item["severity"] in {"high", "critical"} for item in issues),
            "raw": result.text or "",
            "issues": issues,
        }

    async def analyze_video(
        self,
        prompt: str,
        video: bytes,
        audio: bytes,
        *,
        keyframes: list[bytes] | None = None,
        timestamp_offset_seconds: float = 0,
        excerpt_duration_seconds: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Review sampled video frames against the narration/cue context.

        Google-backed fallback review intentionally does not claim to play the
        clip or hear its audio. Audio timing and media duration are verified by
        the local pipeline; this path checks visible frame quality only.
        """
        del video, audio, timestamp_offset_seconds, excerpt_duration_seconds
        if not keyframes:
            raise ValueError("frame-based video review requires sampled keyframes")
        frame_prompt = (
            prompt + "\nFRAME-ONLY REVIEW: only ordered still images are supplied. Do not claim to "
            "have played the video or heard audio. Use the narration and measured cue "
            "boundaries only as context. Do not report synchronization or timing issues "
            "from stills; those are checked separately. Report only clear, high-confidence "
            "visible problems: actual collisions, clipped/unreadable labels, incorrect "
            "equations, or a diagram that contradicts the stated physics. Nested parts of "
            "one diagram are intentional. If a detail is ambiguous, leave it out."
        )
        report = await self.analyze_images(frame_prompt, keyframes, temperature=temperature)
        return {
            "summary": "Frame-only visual review; synchronized audio/video playback was unavailable.",
            "review_mode": "keyframes_only",
            "issues": [
                {
                    **issue,
                    "category": f"video.frame_visual.{issue['category']}",
                }
                for issue in report.get("issues", [])
            ],
        }
