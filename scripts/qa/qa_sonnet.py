"""Verify the configured Sonnet reasoning/output budget with a real render."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.config import get_settings
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.providers import create_code_provider
from scholarmotion.schemas import SceneSpec, VisualBeat


async def main():
    root = Path(".local/qa/sonnet16k")
    root.mkdir(exist_ok=True)
    settings = get_settings()
    scene = SceneSpec(
        scene_id="QA",
        chapter_id="C01",
        title="Force and acceleration",
        learning_objective="Calculate acceleration from force and mass.",
        duration_target_seconds=10,
        narration="A two kilogram cart experiences a six newton net force. Its acceleration is three meters per second squared.",
        equations=["F = ma"],
        visual_beats=[
            VisualBeat(
                beat_id="B01",
                narration_segment="A two kilogram cart experiences a six newton net force.",
                visual="Show a cart with a force arrow. Show mass two kilograms, force six newtons, and acceleration three meters per second squared in separate bands.",
                primitive="diagram",
                start_seconds=0,
                end_seconds=10,
            )
        ],
    )
    result = await generate_scene_code(create_code_provider(settings), scene, [], [])
    (root / "code.py").write_text(result.python_code, encoding="utf-8")
    fallback = "SCHOLARMOTION_DETERMINISTIC_FALLBACK" in result.python_code
    print("Sonnet code returned; deterministic fallback:", fallback, flush=True)
    render = SandboxRenderer(mock_missing=False, timeout_seconds=120).render(
        result.python_code, result.scene_class, root / "render"
    )
    summary = {
        "model": settings.code_model,
        "reasoning": settings.code_reasoning_effort,
        "max_output_tokens": settings.code_max_output_tokens,
        "fallback": fallback,
        "render": asdict(render),
    }
    (root / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Render success:", render.success, flush=True)


asyncio.run(main())
