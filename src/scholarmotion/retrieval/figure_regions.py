"""Find the parts of a paper figure that a scene points at, before its code is written.

Code models estimating box positions from a picture put highlights on legends
and empty space. Models trained to locate things do this far better (Gemini
3.8 Flash boxed 12 of 14 test targets exactly). A box around a curve covers the
whole plot, so a curve is located as its legend entry plus one point on it.
Each box is trimmed to the content inside it; an implausible box is asked of a
second model, and a part neither can place is left out rather than mis-boxed.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from scholarmotion.providers.llm.openai_compatible import _parse_json_object
from scholarmotion.schemas import FigureRegion, PaperFigure

logger = logging.getLogger(__name__)

PROMPT = """A lesson about this paper figure ({label}) will point at parts of it while the narrator says:
"{narration}"
{focus}List each distinct part the lesson should point at (at most 6), in the order it is mentioned.
For each part give its tightest bounding box. If a part is a line or curve in a chart, a box around
it would cover the whole plot: instead give the box of its legend entry and, as its point, the spot
on the curve the narration is about (a peak, dip, jump or end; else its most distinctive spot).
{form}"""

FORMS = {
    "google": (
        'Return JSON only: {{"issues": [], "parts": [{{"name": "...", "box_2d": [ymin, xmin, ymax, '
        'xmax], "point": [y, x] or null}}]}} with coordinates normalized to 0-1000.'
    ),
    "anthropic": (
        "The image is {width}x{height} pixels. Return JSON only: "
        '{{"issues": [], "parts": [{{"name": "...", "bbox": [x0, y0, x1, y1], "point": [x, y] '
        "or null}}]}} in pixels from the top-left corner."
    ),
    "other": (
        'Return JSON only: {{"issues": [], "parts": [{{"name": "...", "bbox_2d": [x0, y0, x1, '
        'y1], "point": [x, y] or null}}]}} with coordinates normalized to 0-1000.'
    ),
}

# A box covering more than this share of the figure points at nothing in particular.
_MAX_AREA = 0.6
_MAX_PARTS = 6


def _family(model: str) -> str:
    return next((name for name in ("google", "anthropic") if model.startswith(f"{name}/")), "other")


def _fractions(part: dict, family: str, size: tuple[int, int]):
    """(box, point) as fractions of the figure from the model's own coordinate format."""
    width, height = size
    if family == "google":
        y0, x0, y1, x1 = (float(v) / 1000 for v in part["box_2d"])
        point = part.get("point")
        point = (float(point[1]) / 1000, float(point[0]) / 1000) if point else None
    elif family == "anthropic":
        x0, y0, x1, y1 = (float(v) for v in part["bbox"])
        x0, x1, y0, y1 = x0 / width, x1 / width, y0 / height, y1 / height
        point = part.get("point")
        point = (float(point[0]) / width, float(point[1]) / height) if point else None
    else:
        x0, y0, x1, y1 = (float(v) / 1000 for v in part["bbox_2d"])
        point = part.get("point")
        point = (float(point[0]) / 1000, float(point[1]) / 1000) if point else None
    box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return box, point


def trim_to_content(image, box, pad: float = 0.012):
    """Shrink a box to the non-background pixels inside it, then pad it slightly.

    Only ever shrinks to what the box already holds, so a correct box stays
    correct; a loose one tightens around its content.
    """
    import numpy as np

    width, height = image.size
    left, top = int(box[0] * width), int(box[1] * height)
    right, bottom = max(left + 1, int(box[2] * width)), max(top + 1, int(box[3] * height))
    pixels = np.asarray(image.convert("RGB"), dtype=int)
    background = pixels[0, 0] if (pixels[0, 0] == pixels[-1, -1]).all() else pixels[-1, -1]
    content = np.abs(pixels[top:bottom, left:right] - background).sum(axis=2) > 60
    rows, columns = np.nonzero(content)
    if not len(columns):
        return None
    return (
        max(0.0, (left + columns.min()) / width - pad),
        max(0.0, (top + rows.min()) / height - pad),
        min(1.0, (left + columns.max() + 1) / width + pad),
        min(1.0, (top + rows.max() + 1) / height + pad),
    )


def _plausible(box) -> bool:
    x0, y0, x1, y1 = box
    return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and (x1 - x0) * (y1 - y0) <= _MAX_AREA


async def _ask(vision, figure: PaperFigure, image, narration: str, names: list[str] | None):
    family = _family(getattr(vision, "model", ""))
    focus = f"The plan says to point at: {figure.focus}\n" if figure.focus else ""
    if names:
        focus += "Locate exactly these parts, using these names: " + "; ".join(names) + "\n"
    prompt = PROMPT.format(
        label=figure.label,
        narration=narration[:1500],
        focus=focus,
        form=FORMS[family].format(width=image.size[0], height=image.size[1]),
    )
    result = await vision.analyze_images(prompt, [Path(figure.path).read_bytes()], temperature=0)
    parsed = _parse_json_object(result.get("raw") or "") or {}
    parts = []
    for part in (parsed.get("parts") or [])[:_MAX_PARTS]:
        try:
            box, point = _fractions(part, family, image.size)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        parts.append((str(part.get("name") or "").strip(), box, point))
    return parts


def _region(image, name: str, box, point) -> FigureRegion | None:
    if not name or not _plausible(box):
        return None
    trimmed = trim_to_content(image, box)
    if trimmed is None:
        return None
    if point is not None and not (0 <= point[0] <= 1 and 0 <= point[1] <= 1):
        point = None
    return FigureRegion(
        name=name,
        box=[round(float(value), 3) for value in trimmed],
        point=[round(float(value), 3) for value in point] if point else None,
    )


async def locate_figure_regions(
    locator, backup, figure: PaperFigure, narration: str
) -> list[FigureRegion]:
    """The parts of ``figure`` this scene points at; empty when none can be placed."""
    if locator is None or not figure.path or not Path(figure.path).exists():
        return []
    from PIL import Image

    image = Image.open(BytesIO(Path(figure.path).read_bytes())).convert("RGB")
    try:
        found = await _ask(locator, figure, image, narration, None)
    except Exception as error:  # noqa: BLE001 - a scene without highlights still teaches
        logger.warning("Could not locate parts of %s: %s", figure.label, error)
        found = []
    regions, retry = [], []
    for name, box, point in found:
        region = _region(image, name, box, point)
        if region is not None:
            regions.append(region)
        elif name:
            retry.append(name)
    if retry and backup is not None:
        try:
            second = await _ask(backup, figure, image, narration, retry)
        except Exception as error:  # noqa: BLE001
            logger.warning("Second look at %s failed: %s", figure.label, error)
            second = []
        for name, box, point in second:
            region = _region(image, name, box, point)
            if region is not None and region.name not in {r.name for r in regions}:
                regions.append(region)
    return regions
