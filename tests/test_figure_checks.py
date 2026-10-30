import json
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from PIL import Image

from scholarmotion.retrieval.figure_check import check_figure_crops
from scholarmotion.retrieval.paper_figures import FigureCrop
from scholarmotion.schemas import PaperFigure, SceneSpec
from scholarmotion.verification.figures import verify_paper_figures_shown


def _pdf(tmp_path):
    document = pdfium.PdfDocument.new()
    document.new_page(600, 800)
    path = tmp_path / "paper.pdf"
    document.save(str(path))
    document.close()
    return path


def _crop(tmp_path, label="Figure 1"):
    path = tmp_path / f"{label.lower().replace(' ', '-')}.png"
    Image.new("RGB", (300, 200), "white").save(path)
    return FigureCrop(
        label=label, page=1, caption=f"{label} | x", path=str(path), width=300, height=200
    )


class Vision:
    """Answers each check from a queue of verdicts, recording the prompts."""

    capabilities = SimpleNamespace(images=True)

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    async def analyze_images(self, prompt, images, *, temperature=None):
        self.prompts.append(prompt)
        assert len(images) == 2  # the crop and its page
        return {"raw": json.dumps(self.answers.pop(0)), "issues": []}


@pytest.mark.asyncio
async def test_a_good_crop_is_kept(tmp_path):
    crop = _crop(tmp_path)
    kept, checks = await check_figure_crops(
        Vision({"verdict": "ok", "issues": []}), _pdf(tmp_path), [crop]
    )
    assert kept == [crop] and checks[0].outcome == "ok"


@pytest.mark.asyncio
async def test_a_crop_with_extra_content_is_redone_from_the_models_box(tmp_path):
    vision = Vision(
        {"verdict": "fix", "figure_box": [100, 500, 900, 900], "issues": ["includes a table"]},
        {"verdict": "ok", "issues": []},
    )
    kept, checks = await check_figure_crops(vision, _pdf(tmp_path), [_crop(tmp_path)])
    assert checks[0].outcome == "recropped" and "table" in checks[0].reason
    # 80% of the page width and 40% of its height, at the render scale.
    assert kept[0].width == pytest.approx(600 * 0.8 * 3, abs=3)
    assert kept[0].height == pytest.approx(800 * 0.4 * 3, abs=3)
    assert Image.open(kept[0].path).size == (kept[0].width, kept[0].height)


@pytest.mark.asyncio
async def test_a_redone_crop_is_kept_unless_it_shows_no_figure(tmp_path):
    fix = {"verdict": "fix", "figure_box": [0.1, 0.5, 0.9, 0.9], "issues": ["has a table"]}
    picky = Vision(fix, {"verdict": "fix", "figure_box": [0.1, 0.5, 0.8, 0.9], "issues": ["x"]})
    kept, checks = await check_figure_crops(picky, _pdf(tmp_path), [_crop(tmp_path)])
    assert len(kept) == 1 and checks[0].outcome == "recropped"

    lost = Vision(fix, {"verdict": "wrong", "issues": ["empty"]})
    kept, checks = await check_figure_crops(lost, _pdf(tmp_path), [_crop(tmp_path)])
    assert kept == [] and checks[0].outcome == "dropped"


@pytest.mark.asyncio
async def test_a_wrong_or_unfixable_crop_is_dropped(tmp_path):
    vision = Vision(
        {"verdict": "wrong", "issues": ["another figure"]},
        {"verdict": "fix", "figure_box": [0.5, 0.5, 0.2, 0.9], "issues": ["bad box"]},
    )
    kept, checks = await check_figure_crops(
        vision, _pdf(tmp_path), [_crop(tmp_path, "Figure 1"), _crop(tmp_path, "Figure 2")]
    )
    assert kept == [] and [check.outcome for check in checks] == ["dropped", "dropped"]


@pytest.mark.asyncio
async def test_a_learner_complaint_reaches_the_model_and_no_vision_keeps_crops(tmp_path):
    vision = Vision({"verdict": "ok", "issues": []})
    await check_figure_crops(vision, _pdf(tmp_path), [_crop(tmp_path)], hint="it is cut off")
    assert "it is cut off" in vision.prompts[0]

    crop = _crop(tmp_path)
    kept, checks = await check_figure_crops(
        SimpleNamespace(capabilities=SimpleNamespace(images=False)), _pdf(tmp_path), [crop]
    )
    assert kept == [crop] and checks[0].outcome == "unchecked"


def _spec(**changes):
    return SceneSpec.model_validate(
        {
            "scene_id": "S11",
            "chapter_id": "C1",
            "duration_target_seconds": 30,
            "visual_beats": [],
            "title": "t",
            "learning_objective": "o",
            "narration": "n",
            "paper_figures": [PaperFigure(label="Figure 3", filename="figure-3.png").model_dump()],
            **changes,
        }
    )


def test_a_scene_must_show_the_figures_it_was_given():
    missing = verify_paper_figures_shown(_spec(), "def construct(self):\n    pass\n")
    assert [issue.category for issue in missing] == ["figure.missing"]
    assert "figure-3.png" in missing[0].suggested_repair

    shown = "image = paper_figure('figure-3.png', 'main')\n"
    assert verify_paper_figures_shown(_spec(), shown) == []
    assert verify_paper_figures_shown(_spec(paper_figures=[]), "x = 1\n") == []
