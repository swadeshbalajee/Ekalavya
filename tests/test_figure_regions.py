"""Paper-figure highlights are located by a vision model and handed to the code model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.retrieval.figure_regions import locate_figure_regions, trim_to_content
from scholarmotion.schemas import FigureRegion, PaperFigure, SceneSpec, VisualBeat


def _figure(tmp_path) -> PaperFigure:
    """A white 400x200 figure with a dark block at x 100-200, y 50-150."""
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).rectangle([100, 50, 199, 149], fill="black")
    path = tmp_path / "figure-5.png"
    image.save(path)
    return PaperFigure(label="Figure 5", filename="figure-5.png", path=str(path), focus="the block")


class _Vision:
    def __init__(self, model: str, parts: list[dict]):
        self.model, self.parts, self.prompts = model, parts, []

    async def analyze_images(self, prompt, images, *, temperature=None):
        self.prompts.append(prompt)
        return {"raw": json.dumps({"issues": [], "parts": self.parts}), "issues": []}


def test_a_loose_box_is_trimmed_to_what_it_holds():
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).rectangle([100, 50, 199, 149], fill="black")
    x0, y0, x1, y1 = trim_to_content(image, (0.1, 0.1, 0.7, 0.9), pad=0)
    assert (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)) == (0.25, 0.25, 0.5, 0.75)
    assert trim_to_content(image, (0.6, 0.1, 0.9, 0.2)) is None  # nothing inside


@pytest.mark.asyncio
async def test_parts_are_read_in_each_models_format_and_bad_boxes_get_a_second_look(tmp_path):
    figure = _figure(tmp_path)
    locator = _Vision(
        "google/gemini-3.8-flash",
        [
            # Gemini: [ymin, xmin, ymax, xmax] and [y, x] in thousandths.
            {"name": "the block", "box_2d": [200, 200, 800, 550], "point": [500, 300]},
            {"name": "the whole thing", "box_2d": [0, 0, 1000, 1000]},
            {"name": "empty space", "box_2d": [100, 700, 200, 900]},
        ],
    )
    backup = _Vision(
        "anthropic/claude-sonnet-5.5",
        [{"name": "the whole thing", "bbox": [90, 40, 210, 160], "point": None}],
    )
    regions = await locate_figure_regions(locator, backup, figure, "Look at the block.")

    names = [region.name for region in regions]
    assert names == ["the block", "the whole thing"]
    block = regions[0]
    assert block.box[0] == pytest.approx(0.25 - 0.012, abs=0.01)
    assert block.point == [0.3, 0.5]
    # Only the parts the first model could not place were asked again, by name.
    assert "the whole thing" in backup.prompts[0] and "empty space" in backup.prompts[0]
    assert "The image is 400x200 pixels" in backup.prompts[0]


@pytest.mark.asyncio
async def test_without_a_locator_or_a_file_nothing_is_located(tmp_path):
    figure = _figure(tmp_path)
    assert await locate_figure_regions(None, None, figure, "x") == []
    missing = figure.model_copy(update={"path": str(tmp_path / "gone.png")})
    assert await locate_figure_regions(_Vision("google/x", []), None, missing, "x") == []


@pytest.mark.asyncio
async def test_measured_highlights_reach_the_code_model_without_the_grid(tmp_path):
    figure = _figure(tmp_path).model_copy(
        update={
            "regions": [
                FigureRegion(name="the legend", box=[0.5, 0.6, 0.9, 0.95]),
                FigureRegion(name="the cyan curve", box=[0.5, 0.8, 0.9, 0.85], point=[0.2, 0.1]),
            ]
        }
    )
    spec = SceneSpec(
        scene_id="S19",
        chapter_id="C1",
        title="Combining experts",
        learning_objective="Read Figure 5",
        narration="Both experts learn fastest.",
        duration_target_seconds=4,
        visual_beats=[VisualBeat(beat_id="b", narration_segment="Both", visual="Figure 5")],
        paper_figures=[figure],
    )
    seen = {}

    class Code:
        name = "openai_compatible"

        async def generate_code(self, prompt, *, cached_prefix=None, purpose="code", images=None):
            seen["prompt"], seen["images"] = prompt, images
            return "from manim import *\nclass SceneS19(Scene):\n    def construct(self):\n        pass\n"

    await generate_scene_code(Code(), spec, narration=spec.narration, sentence_durations=[4.0])

    assert "MEASURED HIGHLIGHTS" in seen["prompt"]
    assert "the legend: figure_region(image, 0.5, 0.6, 0.9, 0.95)" in seen["prompt"]
    assert "figure_point(image, 0.2, 0.1)" in seen["prompt"]
    assert "pink grid" not in seen["prompt"]
    assert seen["images"] == [Path(figure.path).read_bytes()]
