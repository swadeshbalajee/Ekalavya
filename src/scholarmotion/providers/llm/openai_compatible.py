"""LLM, vision, and video-review provider for OpenAI-compatible APIs (e.g. OpenRouter)."""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from scholarmotion.observability import record_provider_event
from scholarmotion.observability.usage import record_failure, record_usage

from .base import LLMCapabilities, call_timeout_seconds
from .thinking import Thinking, thinking_for

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
# Input kinds (text, image, file, ...) of each OpenRouter model, listed once per process.
_MODEL_INPUTS: dict[str, set[str]] | None = None


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        supports_images: bool = False,
        fallback_models: list[str] | None = None,
        code_timeout_seconds: int = 60,
        reasoning_effort: str = "low",
        code_max_output_tokens: int = 6000,
        structured_reasoning_effort: str = "high",
        default_temperature: float = 0.7,
        request_timeout_seconds: int = 90,
        pdf_engine: str = "cloudflare-ai",
        thinking_rules: list[tuple[str, str]] | None = None,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for this provider") from exc
        # Free inference endpoints can stall while shared queues drain. Bound
        # each attempt so the configured fallback models can take over.
        self.request_timeout_seconds = request_timeout_seconds
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=max(90, request_timeout_seconds, code_timeout_seconds, 420),
            max_retries=0,
        )
        self.model = model
        self.openrouter = "openrouter.ai" in str(self.client.base_url).lower()
        self.code_timeout_seconds = code_timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.code_max_output_tokens = code_max_output_tokens
        self.structured_reasoning_effort = structured_reasoning_effort
        self.default_temperature = default_temperature
        self.pdf_engine = pdf_engine
        self.thinking_rules = thinking_rules or []
        self.fallback_models = [
            candidate for candidate in (fallback_models or []) if candidate != model
        ]
        self.capabilities = LLMCapabilities(images=supports_images)

    def _temperature(self, value: float | None) -> float:
        """The caller's temperature, else the configured agent temperature."""
        return getattr(self, "default_temperature", 0.7) if value is None else value

    def _thinking(self, model: str, effort: str | None) -> Thinking:
        """This model's thinking rule; models without one use ``effort``."""
        return thinking_for(model, effort, getattr(self, "thinking_rules", []))

    async def _model_inputs(self, model: str) -> set[str] | None:
        """What ``model`` accepts as input on OpenRouter; None when unknown."""
        global _MODEL_INPUTS
        if _MODEL_INPUTS is None:
            try:
                page = await self.client.models.list()
            except Exception as error:  # noqa: BLE001 - unknown models keep their input
                logger.warning("Could not list OpenRouter models: %s", error)
                return None
            _MODEL_INPUTS = {
                item.id: set(
                    ((item.model_extra or {}).get("architecture") or {}).get("input_modalities", [])
                )
                for item in page.data
            }
        return _MODEL_INPUTS.get(model)

    async def _pdf_engine(self, model: str) -> str:
        """OpenRouter's PDF engine for ``model``: the PDF itself when the model reads
        files, otherwise the configured text conversion."""
        if "file" in (await self._model_inputs(model) or set()):
            return "native"
        return getattr(self, "pdf_engine", "cloudflare-ai")

    async def _reads_images(self, model: str) -> bool:
        """False only for models OpenRouter lists without image input."""
        if not getattr(self, "openrouter", False):
            return True
        inputs = await self._model_inputs(model)
        return inputs is None or "image" in inputs

    @property
    def _provider_label(self) -> str:
        """Provider name shown in usage records."""
        return "openrouter" if getattr(self, "openrouter", False) else self.name

    async def _with_fallback(
        self,
        request,
        *,
        validator=None,
        invalid_output_message="invalid model response",
        timeout_seconds: float = 90,
        purpose: str = "text",
    ):
        last_error: Exception | None = None
        for model in [self.model, *self.fallback_models]:
            started = time.perf_counter()
            try:
                limit = call_timeout_seconds(
                    max(timeout_seconds, getattr(self, "request_timeout_seconds", 90))
                )
                try:
                    async with asyncio.timeout(limit):
                        response = await request(model)
                except TimeoutError as error:
                    # asyncio's timeout has no message; say which model and how long.
                    raise TimeoutError(
                        f"{model} did not answer within {limit:.0f} seconds"
                    ) from error
                status = getattr(response, "status", None)
                if status in {"failed", "incomplete"} or getattr(response, "error", None):
                    raise RuntimeError(
                        f"{model} returned an unfinished Responses API result ({status}); "
                        f"details={getattr(response, 'incomplete_details', None)}"
                    )
                choices = getattr(response, "choices", None)
                if choices and getattr(choices[0], "finish_reason", None) in {
                    "length",
                    "content_filter",
                }:
                    raise RuntimeError(
                        f"{model} returned invalid model response: {choices[0].finish_reason}"
                    )
                # Count every billed attempt, including ones rejected below.
                record_usage(
                    response,
                    purpose=purpose,
                    model=model,
                    provider=self._provider_label,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status="ok" if model == self.model else "fallback",
                )
                if validator is not None and not validator(response):
                    raise RuntimeError(f"{model} returned {invalid_output_message}")
                record_provider_event(provider=self.name, outcome="success")
                return response
            except Exception as error:  # provider SDK errors vary by endpoint
                status_code = getattr(error, "status_code", None)
                retryable = (
                    status_code in {403, 404, 429}
                    or (isinstance(status_code, int) and status_code >= 500)
                    or isinstance(error, TimeoutError)
                    or error.__class__.__name__
                    in {
                        "APITimeoutError",
                        "ConnectTimeout",
                        "ReadTimeout",
                    }
                    or any(
                        marker in str(error)
                        for marker in (
                            "unfinished Responses API result",
                            "invalid model response",
                            "invalid generated Python",
                            "invalid vision JSON",
                        )
                    )
                )
                is_last_model = model == (
                    self.fallback_models[-1] if self.fallback_models else self.model
                )
                if not retryable or is_last_model:
                    record_provider_event(provider=self.name, outcome="error")
                    raise
                last_error = error
                record_failure(
                    purpose=purpose,
                    model=model,
                    provider=self._provider_label,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                record_provider_event(provider=self.name, outcome="fallback")
        if last_error:
            raise last_error
        raise RuntimeError("No model configured for this request")

    async def generate_structured(
        self,
        prompt: str,
        output_schema: type[T],
        *,
        temperature: float | None = None,
        purpose: str | None = None,
    ) -> T:
        def reasoning(model: str) -> dict:
            if not self.openrouter:
                return {}
            value = self._thinking(model, self.structured_reasoning_effort).reasoning()
            return {"reasoning": value} if value else {}

        response = await self._with_fallback(
            lambda model: self.client.responses.parse(
                model=model,
                input=prompt,
                text_format=output_schema,
                temperature=self._temperature(temperature),
                **reasoning(model),
            ),
            purpose=purpose or "structured",
        )
        if response.output_parsed is None:
            raise ValueError(f"{response.model} did not return the requested structured output")
        return response.output_parsed

    async def generate_structured_from_pdf(
        self,
        prompt: str,
        pdf: bytes,
        output_schema: type[T],
        *,
        filename: str = "paper.pdf",
        purpose: str = "paper_outline",
    ) -> T:
        """Read a PDF natively (text, equations, tables, figures) and return JSON."""
        if not self.openrouter:
            raise NotImplementedError("native PDF input needs an OpenRouter endpoint")
        schema = json.dumps(output_schema.model_json_schema())
        content = [
            {
                "type": "file",
                "file": {
                    "filename": filename,
                    "file_data": "data:application/pdf;base64," + base64.b64encode(pdf).decode(),
                },
            },
            {
                "type": "text",
                "text": prompt
                + "\n\nReturn only one JSON object that matches this JSON schema:\n"
                + schema,
            },
        ]

        def valid(result) -> bool:
            parsed = _parse_json_object(_response_text(result))
            try:
                output_schema.model_validate(parsed)
            except Exception:  # noqa: BLE001 - any validation failure retries the next model
                return False
            return True

        async def request(model: str):
            engine = await self._pdf_engine(model)
            thinking = self._thinking(model, "medium")
            return await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=self._temperature(None),
                max_tokens=thinking.output_tokens(32000),
                extra_body={
                    "reasoning": thinking.reasoning(),
                    "plugins": [{"id": "file-parser", "pdf": {"engine": engine}}],
                    "usage": {"include": True},
                },
            )

        response = await self._with_fallback(
            request,
            validator=valid,
            invalid_output_message="invalid model response",
            timeout_seconds=420,
            purpose=purpose,
        )
        return output_schema.model_validate(_parse_json_object(_response_text(response)))

    async def generate_text(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        purpose: str | None = None,
    ) -> str:
        def reasoning(model: str) -> dict:
            if not (self.openrouter and reasoning_effort):
                return {}
            value = self._thinking(model, reasoning_effort).reasoning()
            return {"reasoning": value} if value else {}

        response = await self._with_fallback(
            lambda model: self.client.responses.create(
                model=model,
                input=prompt,
                temperature=self._temperature(temperature),
                **reasoning(model),
            ),
            purpose=purpose or "text",
        )
        return response.output_text

    async def generate_text_with_sources(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        web_search: bool = False,
        max_results: int = 5,
        system: str | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """Answer with optional thinking budget and live web search, returning citations.

        With ``system``, instructions and context go in a system message and
        ``prompt`` is the question alone, so a web search runs on the question
        rather than on the instructions.
        """
        messages = [{"role": "user", "content": prompt}]
        if system:
            messages.insert(0, {"role": "system", "content": system})

        def extra_body(model: str) -> dict[str, Any]:
            body: dict[str, Any] = {}
            if self.openrouter and reasoning_effort:
                value = self._thinking(model, reasoning_effort).reasoning()
                if value:
                    body["reasoning"] = value
            if self.openrouter and web_search:
                body["plugins"] = [{"id": "web", "max_results": max_results}]
            return {"extra_body": body} if body else {}

        response = await self._with_fallback(
            lambda model: self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self._temperature(temperature),
                **extra_body(model),
            ),
            timeout_seconds=180,
            purpose="web_answer" if web_search else "answer",
        )
        message = response.choices[0].message
        annotations = getattr(message, "annotations", None)
        if annotations is None:
            annotations = (getattr(message, "model_extra", None) or {}).get("annotations") or []
        sources: list[dict[str, str]] = []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                annotation = (
                    annotation.model_dump()
                    if hasattr(annotation, "model_dump")
                    else vars(annotation)
                )
            citation = annotation.get("url_citation") or annotation
            url = citation.get("url")
            if url and all(source["url"] != url for source in sources):
                sources.append({"url": url, "title": citation.get("title") or url})
        return _response_text(response), sources

    async def generate_code(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        cached_prefix: list[str] | None = None,
        purpose: str = "code",
        images: list[bytes] | None = None,
    ) -> str:
        """Generate scene code; ``cached_prefix`` blocks precede ``prompt``.

        The prefix holds text shared by every scene (rules, skills, template),
        marked as prompt-cache breakpoints so later scenes and repairs read it
        at the cached-input price instead of paying full input price again.
        """
        if self.openrouter:
            # Use OpenRouter's native chat route for Anthropic code generation.
            # The Responses compatibility route has returned incomplete code
            # despite a generous output budget; never publish its partial text.
            text: str | list[dict[str, Any]] = prompt
            if cached_prefix or images:
                text = [
                    {"type": "text", "text": block, "cache_control": {"type": "ephemeral"}}
                    for block in cached_prefix or []
                    if block
                ] + [{"type": "text", "text": prompt}]
            # Pictures (paper figures, frames of a failed render) go after the cached
            # text, and only to models that can see them.
            pictures = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(image).decode()
                    },
                }
                for image in images or []
            ]

            async def request(model: str):
                content = text + pictures if pictures and await self._reads_images(model) else text
                thinking = self._thinking(model, self.reasoning_effort)
                reasoning = thinking.reasoning()
                return await self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": content}],
                    temperature=self._temperature(temperature),
                    max_tokens=thinking.output_tokens(self.code_max_output_tokens),
                    extra_body={
                        **({"reasoning": reasoning} if reasoning else {}),
                        "usage": {"include": True},
                    },
                )

            response = await self._with_fallback(
                request,
                validator=lambda result: _is_manim_code(_response_text(result)),
                invalid_output_message="invalid generated Python",
                timeout_seconds=self.code_timeout_seconds,
                purpose=purpose,
            )
            return _extract_python_code(_response_text(response))
        if cached_prefix:
            prompt = "\n\n".join([*cached_prefix, prompt])
        request_options = {"max_output_tokens": self.code_max_output_tokens}
        if self.openrouter:
            request_options["reasoning"] = {"effort": self.reasoning_effort}
        response = await self._with_fallback(
            lambda model: self.client.responses.create(
                model=model,
                input=prompt,
                temperature=self._temperature(temperature),
                **request_options,
            ),
            validator=lambda result: _is_manim_code(result.output_text),
            invalid_output_message="invalid generated Python",
            timeout_seconds=self.code_timeout_seconds,
            purpose=purpose,
        )
        return _extract_python_code(response.output_text)

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        if not self.capabilities.images:
            return {"passed": True, "skipped": True, "issues": []}
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for raw in images:
            encoded = base64.b64encode(raw).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )

        def request_options(model: str) -> dict[str, Any]:
            if not getattr(self, "openrouter", False):
                return {"max_tokens": 4096}
            thinking = self._thinking(model, "low")
            return {
                "max_tokens": thinking.output_tokens(4096),
                "response_format": {"type": "json_object"},
                "extra_body": {"reasoning": thinking.reasoning()},
            }

        response = await self._with_fallback(
            lambda model: self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=self._temperature(temperature),
                **request_options(model),
            ),
            validator=lambda result: _has_valid_vision_json(_response_text(result)),
            invalid_output_message="invalid vision JSON",
            timeout_seconds=60,
            purpose="vision_review",
        )
        raw = _response_text(response)
        parsed = _parse_json_object(raw)
        issues = parsed.get("issues", []) if isinstance(parsed, dict) else []
        if not isinstance(issues, list):
            issues = [str(issues)] if issues else []
        normalized_issues = []
        for issue in issues:
            if isinstance(issue, str):
                issue = {"description": issue}
            if not isinstance(issue, dict):
                continue
            normalized_issues.append(
                {
                    "category": issue.get("category", "vision.composition"),
                    "severity": issue.get("severity", "medium"),
                    "confidence": issue.get("confidence", 0.75),
                    "frames": issue.get("frames", []),
                    "objects": issue.get("objects", []),
                    "description": issue.get("description", "Visual issue detected."),
                    "suggested_repair": issue.get(
                        "suggested_repair", "Reposition the visuals to improve clarity."
                    ),
                }
            )
        issues = normalized_issues
        if parsed is None:
            issues = [
                {
                    "category": "vision.invalid_response",
                    "severity": "medium",
                    "confidence": 0.75,
                    "frames": [],
                    "objects": [],
                    "description": "The vision model did not return a valid JSON verification result.",
                    "suggested_repair": "Return a JSON object with an issues array.",
                }
            ]
        return {"passed": not issues, "raw": raw, "issues": issues}

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
        """Review a rendered scene together with its exact narration audio."""
        if not self.openrouter:
            raise RuntimeError("video verification requires an OpenRouter compatible endpoint")
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "video_url",
                "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(video).decode()},
            },
            {
                "type": "input_audio",
                "input_audio": {"data": base64.b64encode(audio).decode(), "format": "wav"},
            },
        ]
        for frame in keyframes or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(frame).decode()
                    },
                }
            )
        try:

            def request(model: str):
                thinking = self._thinking(model, self.reasoning_effort)
                return self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": content}],
                    temperature=self._temperature(temperature),
                    max_tokens=thinking.output_tokens(4096),
                    response_format={"type": "json_object"},
                    extra_body={"reasoning": thinking.reasoning()},
                )

            response = await self._with_fallback(
                request,
                validator=lambda result: _has_valid_review_json(_response_text(result)),
                # Match the retry marker consumed by _with_fallback so malformed
                # Lite output actually advances to the configured vision model.
                invalid_output_message="invalid vision JSON",
                timeout_seconds=120,
                purpose="video_review",
            )
        except Exception as error:
            # Some OpenRouter routes require a higher account-balance floor for
            # video input than for image input. Preserve a useful visual gate
            # when that route is unavailable: the exact synchronized excerpt's
            # sampled frames can still be checked against the supplied narration
            # and cue boundaries. Timing itself remains covered by the local
            # audio/video duration and cue-schedule verifiers.
            if not keyframes or not _is_video_balance_floor_error(error):
                raise
            logger.warning(
                "video input unavailable at provider balance floor; reviewing sampled frames",
                extra={"model": self.model},
            )
            frame_prompt = (
                prompt + "\nFRAME-ONLY FALLBACK: The supplied images are the only visual evidence. "
                "Do not claim to have heard audio or played the video. Use the narration and "
                "measured cue boundaries only as context for what each sampled frame should "
                "show. Do not report synchronization or timing defects from still images; "
                "those are checked separately. Report only clear, high-confidence visible "
                "problems such as a real collision, clipped/unreadable label, incorrect "
                "equation, or a diagram that contradicts the stated physics. Ignore nested "
                "parts that are intentionally inside one diagram. If a detail is ambiguous, "
                "leave it out. Return JSON with an issues array."
            )
            frame_report = await self.analyze_images(
                frame_prompt, keyframes, temperature=temperature
            )
            return {
                "summary": "Frame-only visual review; synchronized audio/video playback was unavailable.",
                "review_mode": "keyframes_only",
                "issues": [
                    {
                        "category": f"video.frame_visual.{issue['category']}",
                        "severity": issue["severity"],
                        "confidence": issue["confidence"],
                        "frames": issue["frames"],
                        "objects": issue["objects"],
                        "description": issue["description"],
                        "suggested_repair": issue["suggested_repair"],
                    }
                    for issue in frame_report.get("issues", [])
                ],
            }
        raw = _response_text(response)
        parsed = _parse_json_object(raw)
        if parsed is None:
            raise RuntimeError("video verifier returned invalid JSON")

        def timing(entry: dict) -> str | None:
            """The entry's time span in scene time; None when it lies outside the excerpt."""
            start = entry.get("timestamp_start_seconds", entry.get("start_seconds"))
            end = entry.get("timestamp_end_seconds", entry.get("end_seconds"))
            if start is None or end is None:
                times = re.findall(r"\d+(?::\d+){1,2}(?:\.\d+)?", str(entry.get("timestamp", "")))
                if len(times) >= 2:
                    start, end = times[:2]
            start_seconds, end_seconds = _seconds(start), _seconds(end)
            if start_seconds is None or end_seconds is None:
                return ""
            if excerpt_duration_seconds is not None and (
                start_seconds < 0
                or end_seconds < start_seconds
                or end_seconds > excerpt_duration_seconds + 0.5
            ):
                return None
            return (
                f" at {timestamp_offset_seconds + start_seconds:.1f}-"
                f"{timestamp_offset_seconds + end_seconds:.1f}s"
            )

        normalized = []
        for issue in parsed.get("issues", []):
            if not isinstance(issue, dict):
                continue
            observed = str(
                next(
                    (
                        issue[key]
                        for key in ("observed", "description", "issue", "problem", "detail")
                        if issue.get(key)
                    ),
                    parsed.get("summary") or "Video QA issue.",
                )
            )
            raw_category = str(issue.get("category", issue.get("type", "quality"))).lower()
            # The reviewer's own category and severity stand; only their spelling is normalized.
            category = re.sub(r"[^a-z0-9_.]", "_", raw_category.strip())
            severity = str(issue.get("severity", "")).lower()
            if severity not in {"low", "medium", "high", "critical"}:
                severity = "medium"
            when = timing(issue)
            if when is None:
                logger.info(
                    "discarding out-of-excerpt video finding",
                    extra={"excerpt_duration_seconds": excerpt_duration_seconds},
                )
                continue
            try:
                confidence = float(issue.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            normalized.append(
                {
                    "category": f"video.{category}",
                    "severity": severity,
                    "confidence": min(1.0, max(0.0, confidence)),
                    "frames": [],
                    "objects": [],
                    "description": observed + when,
                    "suggested_repair": str(
                        issue.get(
                            "concrete_code_change",
                            issue.get("suggested_repair", "Revise the scene and rerun video QA."),
                        )
                    ),
                }
            )
        # A checklist review answers every item; the caller scores it.
        checklist = []
        for item in parsed.get("checklist") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            passed = item.get("pass", item.get("passed", True))
            checklist.append(
                {
                    "id": re.sub(r"[^a-z0-9_]", "_", str(item["id"]).lower().strip()),
                    "passed": passed is not False and str(passed).lower() != "false",
                    "description": str(item.get("observed") or "") + (timing(item) or ""),
                    "suggested_repair": str(
                        item.get("concrete_code_change") or item.get("suggested_repair") or ""
                    ),
                }
            )
        usage = getattr(response, "usage", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        logger.info(
            "video verification completed",
            extra={
                "model": getattr(response, "model", self.model),
                "reasoning_tokens": getattr(completion_details, "reasoning_tokens", None),
                "cost": getattr(usage, "cost", None),
                "issue_count": len(normalized),
            },
        )
        makes_sense = parsed.get("makes_sense")
        return {
            "summary": parsed.get("summary", ""),
            "makes_sense": makes_sense if isinstance(makes_sense, bool) else None,
            "issues": normalized,
            **({"checklist": checklist} if checklist else {}),
        }


def _parse_json_object(value: str) -> dict[str, Any] | None:
    """Accept plain JSON and fenced JSON from compatible vision models."""
    candidate = value.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```\s*$", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # Tolerate a sentence before or after the object.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _response_text(response) -> str:
    raw = response.choices[0].message.content
    if not isinstance(raw, str):
        raw = "\n".join(part.text for part in raw or [] if getattr(part, "type", None) == "text")
    return raw.strip()


def _is_video_balance_floor_error(error: Exception) -> bool:
    """Match OpenRouter's specific video-input balance gate, not general outages."""
    message = str(error).lower()
    return getattr(error, "status_code", None) == 402 and (
        "balance" in message and "video" in message
    )


def _has_valid_vision_json(response_text: str) -> bool:
    parsed = _parse_json_object(response_text)
    return isinstance(parsed, dict) and isinstance(parsed.get("issues"), list)


def _has_valid_review_json(response_text: str) -> bool:
    """A video review answers with an issues list, a checklist, or both."""
    parsed = _parse_json_object(response_text)
    return isinstance(parsed, dict) and (
        isinstance(parsed.get("issues"), list)
        or (isinstance(parsed.get("checklist"), list) and bool(parsed["checklist"]))
    )


def _seconds(value) -> float | None:
    """Seconds from a number or an "m:ss(.f)" / "h:mm:ss" string."""
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    parts = str(value).split(":")
    if not all(part.replace(".", "", 1).isdigit() for part in parts):
        return None
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def _extract_python_code(value: str) -> str:
    fenced = re.search(r"```(?:python)?\s*(.*?)```", value, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    source = value.strip()
    start = re.search(r"(?m)^(?=(?:from|import|class)\s)", source)
    if start:
        source = source[start.start() :]
    return source.strip()


def _is_manim_code(value: str) -> bool:
    try:
        tree = ast.parse(_extract_python_code(value))
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.ClassDef)
        and any(
            (isinstance(base, ast.Name) and base.id == "Scene")
            or (isinstance(base, ast.Attribute) and base.attr == "Scene")
            for base in node.bases
        )
        for node in tree.body
    )
