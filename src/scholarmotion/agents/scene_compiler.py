"""Compiles the curriculum and script into canonical-English scene specifications."""

from __future__ import annotations

from scholarmotion.media.audio import align_visual_beats
from scholarmotion.schemas import NarrationBlock, SceneSpec, VisualBeat


def compile_scene_specs(
    blocks: list[NarrationBlock], storyboard: dict[str, list[VisualBeat]]
) -> list[SceneSpec]:
    specs: list[SceneSpec] = []
    for index, block in enumerate(blocks, 1):
        title = block.title or block.learning_objective.removesuffix(".")
        objective = block.learning_objective
        beats = storyboard[block.block_id]
        # Do not silently inflate an explicitly requested short lesson.  The
        # narration/audio normalizer owns exact delivery timing; this bound
        # only protects rendering from malformed zero-length inputs.
        duration = max(8, min(60, block.estimated_duration_seconds))
        timed = align_visual_beats(block.text, beats, duration)
        specs.append(
            SceneSpec(
                scene_id=f"S{index:02d}",
                chapter_id=block.chapter_id,
                title=title,
                learning_objective=objective,
                duration_target_seconds=duration,
                narration=block.text,
                visual_beats=timed,
                equations=block.equations,
                key_terms=block.key_terms,
                data_plots=block.data_plots,
                paper_figures=block.paper_figures,
                source_ids=block.source_ids,
                pauses=block.pauses,
                start_visual_state=[],
                end_visual_state=(
                    ["completed takeaway visible; hold final diagram while narration concludes"]
                    if index == len(blocks)
                    else ["resolved visual ready for the next scene"]
                ),
                verification=[
                    "no overlap",
                    "subtitle safe area remains clear",
                    "objective is visibly demonstrated",
                    "visuals follow their matching spoken phrases",
                    *(
                        ["natural concluding sentence and settled final image"]
                        if index == len(blocks)
                        else []
                    ),
                ],
                tags=sorted(
                    {beat.primitive.lower() for beat in beats}
                    | ({"lesson_closing"} if index == len(blocks) else set())
                    | ({"data_plot"} if block.data_plots else set())
                    | ({"paper_figure"} if block.paper_figures else set())
                ),
            )
        )
    return specs
