"""Check each paper-figure crop against its page with a vision model.

The cropper finds a figure from its caption and the graphics above it, which
can include a neighbouring table or miss part of the figure. A vision model
sees the crop next to the whole page and either accepts it, gives the box the
figure really occupies (the crop is redone once and kept unless a second look
finds no figure in it), or rejects it, in which case the lesson explains that
figure without the image.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from scholarmotion.providers.llm.openai_compatible import _parse_json_object
from scholarmotion.retrieval.paper_figures import FigureCrop, crop_page_region, render_page

logger = logging.getLogger(__name__)

_CONCURRENT_CHECKS = 4

PROMPT = """You check a figure cropped from a research paper before it is shown in a lesson.
Image 1 is the crop. Image 2 is the whole page it came from.
The crop should show exactly {label} of the paper, whose caption is:
"{caption}"
{hint}Answer with one JSON object:
{{"verdict": "ok" | "fix" | "wrong",
  "figure_box": [x0, y0, x1, y1],
  "issues": [{{"description": "..."}}]}}
- "ok": the crop shows the whole of {label} (every panel, axis label and legend) and nothing
  else of note (no table, body text or other figure). Its caption may be left out.
- "fix": {label} is on the page but the crop cuts part of it off or includes other content.
  Give figure_box: the region of {label} on image 2 as whole numbers from 0 to 1000
  (thousandths of the page width and height, measured from its top-left corner), covering
  every panel, axis label and legend but not the caption text, body text, tables or other
  figures.
- "wrong": image 2 does not contain {label}.
issues lists what is wrong; use an empty list for "ok"."""


@dataclass
class FigureCheck:
    label: str
    outcome: str  # "ok", "recropped", "dropped" or "unchecked"
    reason: str = ""


def _box(value) -> tuple[float, float, float, float] | None:
    """The figure box as page fractions, from thousandths (or fractions, if all are <= 1)."""
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if max(values) > 1:
        values = [item / 1000 for item in values]
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return x0, y0, x1, y1


async def _ask(vision, crop: FigureCrop, page_png: bytes, hint: str) -> dict:
    result = await vision.analyze_images(
        PROMPT.format(
            label=crop.label,
            caption=crop.caption,
            hint=f"A learner reported: {hint!r}. Take it into account.\n" if hint else "",
        ),
        [Path(crop.path).read_bytes(), page_png],
        temperature=0,
    )
    if result.get("skipped"):
        return {"verdict": "unchecked"}
    parsed = _parse_json_object(result.get("raw") or "")
    if not isinstance(parsed, dict):
        return {"verdict": "unchecked"}
    issues = parsed.get("issues") or []
    parsed["reason"] = "; ".join(
        str(issue.get("description", issue)) if isinstance(issue, dict) else str(issue)
        for issue in issues
    )
    return parsed


async def _check_one(
    vision, pdf_path: str | Path, crop: FigureCrop, hint: str
) -> tuple[FigureCrop | None, FigureCheck]:
    try:
        page_png = await asyncio.to_thread(render_page, pdf_path, crop.page)
        answer = await _ask(vision, crop, page_png, hint)
        verdict = answer.get("verdict")
        if verdict == "ok" or verdict == "unchecked":
            return crop, FigureCheck(crop.label, verdict)
        box = _box(answer.get("figure_box"))
        if verdict != "fix" or box is None:
            return None, FigureCheck(crop.label, "dropped", answer.get("reason", ""))
        redone = await asyncio.to_thread(crop_page_region, pdf_path, crop, box)
        # The model's own box is at least as good as the caption-based one, so the
        # second look only rules out a crop that does not show the figure at all.
        second = await _ask(vision, redone, page_png, hint)
        if second.get("verdict") == "wrong":
            return None, FigureCheck(crop.label, "dropped", second.get("reason", ""))
        return redone, FigureCheck(crop.label, "recropped", answer.get("reason", ""))
    except Exception as error:  # noqa: BLE001 - an unchecked figure beats a failed lesson
        logger.warning("Could not check %s: %s", crop.label, error)
        return crop, FigureCheck(crop.label, "unchecked", str(error)[:200])


async def check_figure_crops(
    vision, pdf_path: str | Path, crops: list[FigureCrop], *, hint: str = ""
) -> tuple[list[FigureCrop], list[FigureCheck]]:
    """Keep the crops that show their figure, re-cropping where the model gives a box.

    ``hint`` is a learner's complaint about a figure, passed to the model as-is.
    Without a vision-capable model every crop is kept unchecked.
    """
    if vision is None or not getattr(getattr(vision, "capabilities", None), "images", False):
        return crops, [FigureCheck(crop.label, "unchecked") for crop in crops]
    semaphore = asyncio.Semaphore(_CONCURRENT_CHECKS)

    async def bounded(crop: FigureCrop):
        async with semaphore:
            return await _check_one(vision, pdf_path, crop, hint)

    results = await asyncio.gather(*(bounded(crop) for crop in crops))
    kept = [crop for crop, _ in results if crop is not None]
    return kept, [check for _, check in results]
