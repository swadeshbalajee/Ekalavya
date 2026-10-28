"""Persist the usage ledger as per-call rows, and name the module each call belongs to."""

from __future__ import annotations

from uuid import UUID, uuid4

from scholarmotion.observability.usage import UsageLedger
from scholarmotion.persistence.models import UsageEvent

# (substring of the purpose or stage, dashboard module), first match wins.
_MODULES = (
    ("code_repair", "Code repair"),
    ("video_review", "Video review"),
    ("vision_review", "Vision review"),
    ("lesson_review", "Lesson review"),
    ("translation", "Translation"),
    ("narration", "Narration (TTS)"),
    ("tts", "Narration (TTS)"),
    ("stt", "Voice input"),
    ("paper_outline", "Paper outline"),
    ("storyboard", "Storyboard"),
    ("script", "Script"),
    ("chat", "Chat & planning"),
    ("web_answer", "Chat & planning"),
    ("code", "Manim code"),
)


def module_for(purpose: str, stage: str | None = None) -> str:
    """The dashboard module of a call, from its ``stage:purpose`` label."""
    label = f"{stage or ''}:{purpose}".lower()
    return next((module for key, module in _MODULES if key in label), "Other")


def new_run_id() -> str:
    return uuid4().hex


def usage_rows(
    ledger: UsageLedger,
    *,
    user_id: UUID | None,
    project_id: UUID | None,
    operation: str,
    run_id: str,
) -> list[UsageEvent]:
    """One row per ledger entry: tokens, cost, time taken and outcome of each call."""
    rows = []
    for entry in list(ledger.entries):
        stage = entry.purpose.rpartition(":")[0]
        rows.append(
            UsageEvent(
                created_at=entry.started_at,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                operation=operation,
                stage=stage or None,
                purpose=entry.purpose,
                scene_key=entry.scene,
                provider=entry.provider or None,
                model=entry.model,
                status=entry.status,
                calls=1,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                cached_tokens=entry.cached_tokens,
                reasoning_tokens=entry.reasoning_tokens,
                characters=entry.characters,
                cost_usd=entry.cost_usd,
                latency_ms=entry.latency_ms,
            )
        )
    return rows
