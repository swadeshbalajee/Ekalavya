"""Crop a paper's own figures out of its PDF so scenes can show them.

A figure is located by its caption ("Figure 3:" / "Fig. 3."): the graphics
(embedded images and vector paths) between that caption and the caption or
page edge above it form the figure, together with the sub-labels just above
the caption. The crop is rendered at high resolution as a PNG.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CAPTION = re.compile(r"(?m)^\s*(Figure|Fig\.)\s*(\d+)\s*[:.]")
# PDFium page object types.
_PATH, _IMAGE = 2, 3
RENDER_SCALE = 3.0
MIN_FIGURE_POINTS = 40.0


@dataclass
class FigureCrop:
    label: str  # "Figure 3"
    page: int
    caption: str
    path: str
    width: int
    height: int

    def as_dict(self) -> dict:
        return asdict(self)


def figure_label(value: str) -> str | None:
    """Normalise 'Fig. 3', 'figure 3(b)' or 'Figure 3' to 'Figure 3'."""
    match = re.search(r"\bfig(?:ure|\.)?\s*(\d+)", value, re.IGNORECASE)
    return f"Figure {match.group(1)}" if match else None


def _caption_boxes(page, textpage) -> list[tuple[str, str, tuple[float, float, float, float]]]:
    """(label, caption text, (left, bottom, right, top)) for each caption on the page."""
    text = textpage.get_text_range()
    found = []
    for match in _CAPTION.finditer(text):
        start = match.start(1)
        boxes = [textpage.get_charbox(index) for index in range(start, match.end())]
        boxes = [box for box in boxes if box[2] > box[0]]
        if not boxes:
            continue
        box = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        caption = " ".join(text[match.start(1) : match.start(1) + 300].split())
        caption = re.split(r"(?<=\.)\s", caption, maxsplit=1)[0]
        found.append((f"Figure {match.group(2)}", caption, box))
    return found


def _graphics(page) -> list[tuple[float, float, float, float]]:
    width, height = page.get_size()
    boxes = []
    for obj in page.get_objects():
        if obj.type not in (_PATH, _IMAGE):
            continue
        left, bottom, right, top = obj.get_bounds()
        # Running headers/footers (and their rules) live in the page margins.
        if bottom > height * 0.93 or top < height * 0.06:
            continue
        if obj.type == _PATH:
            thin = min(right - left, top - bottom) < 2
            # Dots, and rules spanning most of the text width, are not figures.
            if (right - left < 4 and top - bottom < 4) or (thin and right - left > width * 0.6):
                continue
        boxes.append((left, bottom, right, top))
    return boxes


def extract_figures(
    pdf_path: str | Path, out_dir: str | Path, *, labels: set[str] | None = None
) -> list[FigureCrop]:
    """Crop each captioned figure (or only ``labels``) to ``out_dir`` as PNG."""
    import pypdfium2 as pdfium

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    crops: list[FigureCrop] = []
    seen: set[str] = set()
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            captions = sorted(_caption_boxes(page, textpage), key=lambda item: -item[2][3])
            graphics = _graphics(page)
            for position, (label, caption, box) in enumerate(captions):
                if label in seen or (labels is not None and label not in labels):
                    continue
                caption_top = box[3]
                # The figure sits between this caption and the caption above it.
                ceiling = captions[position - 1][2][1] if position else height
                members = [
                    g
                    for g in graphics
                    if g[1] >= caption_top - 2
                    and g[3] <= ceiling + 2
                    and (g[2] - g[0]) * (g[3] - g[1]) > 16
                ]
                if not members:
                    continue
                left = min(g[0] for g in members)
                right = max(g[2] for g in members)
                top = min(max(g[3] for g in members), ceiling)
                # Sub-figure labels like "(a)" sit between graphics and caption.
                bottom = caption_top + 1
                if right - left < MIN_FIGURE_POINTS or top - bottom < MIN_FIGURE_POINTS:
                    continue
                pad = 4.0
                left, right = max(0.0, left - pad), min(width, right + pad)
                bottom, top = max(0.0, bottom), min(height, top + pad)
                image = page.render(
                    scale=RENDER_SCALE,
                    crop=(left, bottom, width - right, height - top),
                ).to_pil()
                slug = label.lower().replace(" ", "-")
                target = output / f"{slug}.png"
                image.save(target)
                seen.add(label)
                crops.append(
                    FigureCrop(
                        label=label,
                        page=page_index + 1,
                        caption=caption[:300],
                        path=str(target),
                        width=image.width,
                        height=image.height,
                    )
                )
    finally:
        document.close()
    return crops
