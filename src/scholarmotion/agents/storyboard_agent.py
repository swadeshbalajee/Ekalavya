"""Plans the visual beats of each scene so animation follows the narration."""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field

from scholarmotion.schemas import NarrationBlock, VisualBeat

logger = logging.getLogger(__name__)


class PlannedBeat(BaseModel):
    visual: str
    primitive: str = "diagram"
    narration_segment: str = ""


class SceneVisualPlan(BaseModel):
    block_id: str
    design_summary: str = ""
    beats: list[PlannedBeat] = Field(min_length=2, max_length=6)


class LessonVisualPlan(BaseModel):
    scenes: list[SceneVisualPlan] = Field(min_length=1, max_length=20)


def _fallback_beats(block: NarrationBlock) -> list[PlannedBeat]:
    """When visual planning is unavailable: show each equation, then a labelled diagram."""
    beats = [
        PlannedBeat(
            visual=f"Write the equation {equation} and label each symbol", primitive="equation"
        )
        for equation in block.equations[:3]
    ]
    beats.append(
        PlannedBeat(
            visual="Draw a simple labelled diagram of the idea in this narration",
            primitive="diagram",
        )
    )
    return beats


_VISUAL_PLANNING_RULES = """Design an actual teaching animation, not a slide deck. Think through the learner's mental model before choosing visuals. Do not introduce or prescribe an analogy unless the learner explicitly requested one. Begin with a concrete physical setup, observable mechanism, or data example, then progressively connect it to the formal representation. Include a small worked example with explicit values when the concept supports one. Show cause and effect by changing one quantity at a time, and end with a check, misconception contrast, or compact synthesis. For a research paper, ground each claim in supplied evidence and show a paper figure/equation or toy example that explains the mechanism.

The output describes visuals and animation actions only. Spoken narration is delivered as audio and subtitles. NEVER put the narration, a sentence copied from narration, a caption, or a spoken sentence into a Manim Text object. Screen text is limited to concise titles, 1–5 word labels, symbols, equations, and short source/page badges. Preserve canonical STEM notation. Keep subtitles unobstructed. A beat must name visible objects, their layout, what changes, and the intended learner inference; avoid vague directions such as 'show a diagram'. Return 3–6 ordered visual beats for one narrated segment. If a worked example uses numbers, derive every displayed value from the narration and check the arithmetic; never introduce a conflicting set of values. A toy example may add values only when the narration clearly frames them as illustrative. Across scenes, explicitly state the visual progression: setup, mechanism, quantitative check, and transfer or misconception check."""


def _plot_lines(block: NarrationBlock) -> str:
    return "".join(
        f"Data plot {plot.plot_id} ({plot.kind} chart from {plot.source or 'the paper'}, exact "
        f"values): {plot.title}; categories {plot.categories}; "
        + "; ".join(f"{series.name} {series.values}" for series in plot.series)
        + "\n"
        for plot in block.data_plots
    ) + "".join(
        f"Paper figure {figure.label} (the ORIGINAL image is shown on screen): {figure.caption}"
        + (f" Point at, in order: {figure.focus}" if figure.focus else "")
        + "\n"
        for figure in block.paper_figures
    )


STORYBOARD_BATCH = 8


async def _plan_batch(
    provider,
    blocks: list[NarrationBlock],
    *,
    earlier: list[NarrationBlock],
    section_notes: dict[str, str],
    looked_up: str,
) -> dict[str, SceneVisualPlan]:
    lesson_outline = "\n\n".join(
        f"SCENE {block.block_id}\nLearning objective: {block.learning_objective}\n"
        f"Equations: {block.equations or '(none)'}\nSource IDs: {block.source_ids or '(none)'}\n"
        + (
            f"Lesson plan for this part: {section_notes[block.chapter_id]}\n"
            if block.chapter_id in section_notes
            else ""
        )
        + f"{_plot_lines(block)}"
        f"Audio/subtitle narration for alignment only (never copy to screen): {block.text}"
        for block in blocks
    )
    context = (
        "This batch continues a longer lecture. Earlier scenes, already planned: "
        + "; ".join(
            f"{block.block_id} {block.title or block.learning_objective}" for block in earlier
        )
        + ".\n\n"
        if earlier
        else ""
    )
    prompt = (
        f"{_VISUAL_PLANNING_RULES}\n\n{context}Plan the whole lesson as one coherent sequence. "
        "Return one scene entry per supplied block, retaining its exact block_id. Across the "
        "lesson, avoid repeating the same visual; build from intuition to formal reasoning and "
        "a worked example. Use only equations listed for that block; do not invent alternate "
        "forms. For a paper, use the supplied source IDs and avoid unsupported claims. When a "
        "scene lists a data plot, one beat must draw that chart with exactly those values, "
        "then highlight the comparison the narration makes. When a scene lists a paper "
        "figure, show that original image and plan beats that highlight its parts in the "
        "order the narration names them; do not redraw it.\n\n"
        'Return valid JSON only with this shape: {"scenes":[{"block_id":"N01",'
        '"design_summary":"...","beats":[{"visual":"...","primitive":"diagram",'
        '"narration_segment":"exact spoken phrase for this beat"}]}]}. '
        "Every beat must quote the corresponding phrase from its narration in narration_segment, "
        "in spoken order. Align each object change with that phrase. "
        "Include every supplied block_id exactly once.\n\n"
        + (
            "The lesson plan's visual ideas are suggestions from the curator who read the "
            "material: use them where they fit the narration. "
            if section_notes
            else ""
        )
        + (f"\n\nPASSAGES YOU LOOKED UP:\n{looked_up}" if looked_up else "")
        + f"\n\n{lesson_outline}\n\nReturn the scenes list."
    )
    try:
        raw = await provider.generate_text(prompt, reasoning_effort="high")
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        lesson_plan = LessonVisualPlan.model_validate(json.loads(cleaned))
        return {plan.block_id: plan for plan in lesson_plan.scenes}
    except Exception as error:  # noqa: BLE001 - provider errors should not erase a lesson
        logger.warning("Lesson visual plan was unreadable, asking once more: %s", error)
    # A long plan sometimes comes back as broken JSON; structured output asks again
    # in a form the provider checks, which costs far less than generic visuals.
    try:
        lesson_plan = await provider.generate_structured(prompt, LessonVisualPlan)
        return {plan.block_id: plan for plan in lesson_plan.scenes}
    except Exception as error:  # noqa: BLE001 - provider errors should not erase a lesson
        logger.warning("Lesson visual planning failed; using topic fallbacks: %s", error)
        return {}


async def plan_storyboard(
    provider,
    blocks: list[NarrationBlock],
    *,
    section_notes: dict[str, str] | None = None,
    looked_up: str = "",
) -> dict[str, list[VisualBeat]]:
    """Plan specific, pedagogical visual beats, with a deterministic safe fallback.

    ``section_notes`` maps a chapter id to its part of the curator's plan (key points
    and visual ideas); ``looked_up`` holds passages the planner asked for.
    """
    plans: dict[str, SceneVisualPlan] = {}
    if getattr(provider, "name", "") != "mock" and hasattr(provider, "generate_text"):
        # Long lectures are planned in batches; each batch sees the titles of
        # the scenes before it so visuals keep building instead of repeating.
        for offset in range(0, len(blocks), STORYBOARD_BATCH):
            batch = blocks[offset : offset + STORYBOARD_BATCH]
            plans.update(
                await _plan_batch(
                    provider,
                    batch,
                    earlier=blocks[:offset],
                    section_notes=section_notes or {},
                    looked_up=looked_up,
                )
            )

    result: dict[str, list[VisualBeat]] = {}
    for block in blocks:
        plan = plans.get(block.block_id)
        planned = plan.beats if plan and plan.beats else _fallback_beats(block)
        count = len(planned)
        result[block.block_id] = [
            VisualBeat(
                beat_id=f"{block.block_id}-b{index + 1}",
                narration_segment=beat.narration_segment or block.text,
                visual=beat.visual,
                primitive=beat.primitive,
                start_seconds=block.estimated_duration_seconds * index / count,
                end_seconds=block.estimated_duration_seconds * (index + 1) / count,
            )
            for index, beat in enumerate(planned)
        ]
    return result


def create_storyboard(blocks: list[NarrationBlock]) -> dict[str, list[VisualBeat]]:
    """Offline/test storyboard generated with the same explicit quality rubric."""
    result = {}
    for block in blocks:
        planned = _fallback_beats(block)
        count = len(planned)
        result[block.block_id] = [
            VisualBeat(
                beat_id=f"{block.block_id}-b{index + 1}",
                narration_segment=block.block_id,
                visual=beat.visual,
                primitive=beat.primitive,
                start_seconds=block.estimated_duration_seconds * index / count,
                end_seconds=block.estimated_duration_seconds * (index + 1) / count,
            )
            for index, beat in enumerate(planned)
        ]
    return result
