import json

import pytest

from scholarmotion.agents.storyboard_agent import (
    plan_storyboard,
)
from scholarmotion.schemas import NarrationBlock


def _block(block_id: str, *, narration: str, equations: list[str] | None = None):
    return NarrationBlock(
        block_id=block_id,
        chapter_id="C01",
        text=narration,
        learning_objective="Explain a technical concept using a concrete example.",
        estimated_duration_seconds=40,
        equations=equations or [],
    )


@pytest.mark.asyncio
async def test_model_visual_planning_is_a_separate_step_and_does_not_render_narration():
    class Planner:
        name = "live"
        prompt = ""

        async def generate_text(self, prompt, *, temperature=None, reasoning_effort=None):
            self.prompt = prompt
            assert temperature is None  # the configured agent temperature applies
            assert reasoning_effort == "high"
            return json.dumps(
                {
                    "scenes": [
                        {
                            "block_id": "N01",
                            "design_summary": "A point charge and a growing spherical surface.",
                            "beats": [
                                {
                                    "visual": "Show one positive charge inside a sphere",
                                    "primitive": "diagram",
                                },
                                {
                                    "visual": "Double enclosed charge and field-line count",
                                    "primitive": "worked_example",
                                },
                            ],
                        }
                    ]
                }
            )

    block = _block("N01", narration="The exact spoken sentence is audio and subtitles only.")
    planner = Planner()
    storyboard = await plan_storyboard(planner, [block])

    assert "The exact spoken sentence is audio" in planner.prompt
    assert "never copy to screen" in planner.prompt
    assert storyboard["N01"][1].visual == "Double enclosed charge and field-line count"


@pytest.mark.asyncio
async def test_a_broken_visual_plan_is_asked_for_again_before_generic_visuals():
    from scholarmotion.agents.storyboard_agent import LessonVisualPlan

    class Planner:
        name = "live"

        async def generate_text(self, prompt, *, temperature=None, reasoning_effort=None):
            return '{"scenes": [{"block_id": "N01" "beats": []}'

        async def generate_structured(self, prompt, schema):
            assert schema is LessonVisualPlan
            return LessonVisualPlan.model_validate(
                {
                    "scenes": [
                        {
                            "block_id": "N01",
                            "design_summary": "Prism",
                            "beats": [
                                {"visual": "A prism splits white light"},
                                {"visual": "Blue bends most"},
                            ],
                        }
                    ]
                }
            )

    storyboard = await plan_storyboard(Planner(), [_block("N01", narration="Light splits.")])
    assert storyboard["N01"][0].visual == "A prism splits white light"
