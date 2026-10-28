"""Plan a full-paper lecture: read one paper and outline it chapter by chapter.

The outline follows the paper's own structure and carries what each chapter
must show verbatim: the paper's equations (as MathTex-ready LaTeX) and the
numbers from its tables, which scenes redraw as charts.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from scholarmotion.retrieval.paper_figures import figure_label
from scholarmotion.schemas import CurriculumItem, CurriculumPlan, DataPlot, PaperFigure

logger = logging.getLogger(__name__)

# Bounds for a whole-paper lecture when the learner does not name a length.
PAPER_MIN_MINUTES = 6.0
PAPER_MAX_MINUTES = 30.0
PAPER_DEFAULT_MINUTES = 12.0
# Text sent when a provider cannot read the PDF file itself.
PAPER_TEXT_LIMIT = 120_000


class PaperChapter(BaseModel):
    title: str
    objective: str
    paper_sections: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    plots: list[DataPlot] = Field(default_factory=list)
    figure_notes: list[str] = Field(default_factory=list)
    show_figures: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, gt=0, le=5)


class PaperOutline(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    one_sentence_summary: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    recommended_minutes: float = PAPER_DEFAULT_MINUTES
    chapters: list[PaperChapter]
    # Filled in after planning from the PDF itself, never by the model.
    figure_crops: list[PaperFigure] = Field(default_factory=list)


OUTLINE_PROMPT = """You plan a detailed narrated video lecture that explains ONE research paper,
attached below, from start to finish for a motivated student.

Read the whole paper, including equations, tables, figures and their captions. Return an outline
whose chapters follow the paper's own logic, typically: the problem and why it matters; the
background a student needs; the method, one chapter per key idea or component; the experiments;
the results; limitations and the takeaway. Merge or split chapters so each teaches one idea.

For every chapter give:
- title: short (at most 7 words) and objective: what the student will understand afterwards.
- paper_sections: the section numbers/names it covers.
- key_points: 3-6 specific facts, claims, design choices or numbers stated in the paper that the
  narration must teach. Be concrete; never generic.
- equations: the paper's important equations for this chapter, copied exactly in meaning, as
  single-line MathTex-ready LaTeX (no \\label, no equation/align environments, no equation numbers).
  Keep the paper's symbols. Only equations that this chapter explains.
- plots: charts redrawn from numbers printed in the paper's tables or text, never estimated from
  a figure and never invented. kind is "bar" (compare methods/settings) or "line" (a trend over an
  ordered x). categories are the x labels (at most 6, short); each series has exactly one value
  per category (at most 3 series). source names the table, e.g. "Table 2". Give plot_id values
  P1, P2, ... unique across the whole outline. Include plots only for results the lecture discusses.
- show_figures: labels of the paper's own figures to show on screen as the original image in
  this chapter, exactly as the paper labels them ("Figure 1"). Show a figure when the student
  should see the real thing: architecture diagrams, environments/datasets, result plots, heat
  maps, qualitative examples. Each figure belongs to the one chapter that explains it.
- figure_notes: for each shown figure, which parts the narration should point at and in what
  order (e.g. "Figure 1(a): base network on the left, attention network, weighted sum K_T").
  For a figure not shown as an image, a description of what to redraw schematically.
- weight: relative share of lecture time (1 = average; core method chapters usually 1.5-2).

Also return the paper title, authors, a one-sentence summary, prerequisites a student should know,
and recommended_minutes: the lecture length that covers the paper in detail without padding
({min_minutes:.0f}-{max_minutes:.0f}; dense or long papers need more).

STUDENT REQUEST: {request}
{duration_note}
"""


def _clamp_minutes(value: float) -> float:
    return max(PAPER_MIN_MINUTES, min(PAPER_MAX_MINUTES, float(value)))


def _clean(outline: PaperOutline) -> PaperOutline:
    """Drop malformed plots and empty chapters; keep plot ids unique."""
    seen: set[str] = set()
    chapters = []
    for chapter in outline.chapters:
        if not chapter.title.strip():
            continue
        plots = []
        for plot in chapter.plots:
            if not plot.is_consistent() or len(plot.categories) > 8:
                logger.info("Dropping inconsistent paper plot %s", plot.plot_id)
                continue
            plot_id = plot.plot_id if plot.plot_id not in seen else f"P{len(seen) + 1}"
            seen.add(plot_id)
            plots.append(plot.model_copy(update={"plot_id": plot_id}))
        chapters.append(chapter.model_copy(update={"plots": plots}))
    return outline.model_copy(update={"chapters": chapters})


def fallback_outline(title: str, chunks: list[dict]) -> PaperOutline:
    """Offline outline from parsed section headings (mock providers, outages)."""
    sections: list[tuple[str, list[str]]] = []
    for chunk in chunks:
        section = (chunk.get("section") or "").strip()
        if not section:
            continue
        if not sections or sections[-1][0] != section:
            sections.append((section, []))
        if chunk.get("content_type") == "paragraph" and len(sections[-1][1]) < 4:
            sections[-1][1].append(str(chunk.get("text", ""))[:300])
    ignored = re.compile(r"references|acknowledg|appendix|bibliograph", re.IGNORECASE)
    chapters = [
        PaperChapter(
            title=name[:60],
            objective=f"Explain what the paper presents in {name}.",
            paper_sections=[name],
            key_points=points,
        )
        for name, points in sections
        if not ignored.search(name)
    ][:10]
    if not chapters:
        chapters = [
            PaperChapter(
                title=title[:60] or "The paper",
                objective="Explain the paper's problem, method and results.",
                key_points=[str(chunk.get("text", ""))[:300] for chunk in chunks[:5]],
            )
        ]
    return PaperOutline(title=title, chapters=chapters)


async def outline_paper(
    llm,
    *,
    title: str,
    request: str,
    pdf_path: str | Path | None,
    chunks: list[dict],
    target_minutes: float | None = None,
) -> PaperOutline:
    """Read the paper (the PDF itself when the provider supports files)."""
    if getattr(llm, "name", "") == "mock":
        return fallback_outline(title, chunks)
    duration_note = (
        f"The student asked for about {target_minutes:.0f} minutes; set recommended_minutes to that."
        if target_minutes
        else "The student did not ask for a length."
    )
    prompt = OUTLINE_PROMPT.format(
        request=request,
        duration_note=duration_note,
        min_minutes=PAPER_MIN_MINUTES,
        max_minutes=PAPER_MAX_MINUTES,
    )
    outline: PaperOutline | None = None
    pdf = Path(pdf_path) if pdf_path else None
    if pdf is not None and pdf.exists() and hasattr(llm, "generate_structured_from_pdf"):
        try:
            outline = await llm.generate_structured_from_pdf(
                prompt, pdf.read_bytes(), PaperOutline, filename=pdf.name
            )
        except Exception as error:  # noqa: BLE001 - fall back to the parsed text
            logger.warning("Reading the paper PDF failed; using extracted text: %s", error)
    if outline is None:
        text = "\n".join(
            f"[p{chunk.get('page')}] [{chunk.get('section') or ''}] {chunk.get('text', '')}"
            for chunk in chunks
        )[:PAPER_TEXT_LIMIT]
        try:
            outline = await llm.generate_structured(
                prompt + "\nPAPER TEXT (extracted; equations may be garbled, reconstruct them "
                "from context):\n" + text,
                PaperOutline,
            )
        except Exception as error:  # noqa: BLE001 - a lecture still follows the headings
            logger.warning("Paper outline failed; using section headings: %s", error)
            return fallback_outline(title, chunks)
    outline = _clean(outline).model_copy(update={"figure_crops": []})
    if not outline.chapters:
        return fallback_outline(title, chunks)
    return outline


def lecture_minutes(outline: PaperOutline, requested: float | None) -> float:
    """The learner's explicit length wins; otherwise the outline's recommendation."""
    return _clamp_minutes(requested or outline.recommended_minutes or PAPER_DEFAULT_MINUTES)


def curriculum_from_outline(
    outline: PaperOutline, *, minutes: float, source_ids: list[str] | None = None
) -> CurriculumPlan:
    total_seconds = minutes * 60
    weight_sum = sum(chapter.weight for chapter in outline.chapters) or 1.0
    items = [
        CurriculumItem(
            title=chapter.title,
            objective=chapter.objective,
            duration_seconds=max(20.0, total_seconds * chapter.weight / weight_sum),
            prerequisites=[item.title for item in outline.chapters[:index]],
            source_ids=list(source_ids or [])[:8],
            key_points=[
                *chapter.key_points,
                *(f"Figure to redraw: {note}" for note in chapter.figure_notes),
            ],
            equations=chapter.equations,
            plots=chapter.plots,
            figures=_chapter_figures(outline, chapter),
        )
        for index, chapter in enumerate(outline.chapters)
    ]
    return CurriculumPlan(topic=outline.title, items=items)


def _chapter_figures(outline: PaperOutline, chapter: PaperChapter) -> list[PaperFigure]:
    """The cropped figures this chapter shows, with its notes on what to point at."""
    crops = {crop.label: crop for crop in outline.figure_crops}
    figures = []
    for value in chapter.show_figures:
        label = figure_label(value)
        crop = crops.get(label or "")
        if crop is None or any(figure.label == crop.label for figure in figures):
            continue
        number = re.escape(label.split()[-1])
        focus = " ".join(
            note
            for note in chapter.figure_notes
            if re.search(rf"\bfig(?:ure|\.)?\s*{number}\b", note, re.IGNORECASE)
        )
        figures.append(crop.model_copy(update={"focus": focus}))
    return figures


def referenced_figures(outline: PaperOutline) -> set[str]:
    return {
        label
        for chapter in outline.chapters
        for value in chapter.show_figures
        if (label := figure_label(value))
    }


def outline_evidence(outline: PaperOutline) -> list[dict]:
    """Compact grounding for the script writer and reviewer."""
    evidence = [
        {
            "kind": "paper",
            "content": f"{outline.title} by {', '.join(outline.authors) or 'unknown authors'}. "
            f"{outline.one_sentence_summary}",
        }
    ]
    for chapter in outline.chapters:
        evidence.append(
            {
                "kind": "chapter",
                "content": f"{chapter.title} ({', '.join(chapter.paper_sections)}): "
                + " | ".join(chapter.key_points),
                "equations": chapter.equations,
                "plots": [plot.model_dump() for plot in chapter.plots],
            }
        )
    return evidence
