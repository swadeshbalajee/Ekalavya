from manim import *

from scholarmotion.manim_runtime.components import (
    FONT,
    REGIONS,
    CueClock,
    SafeTitle,
    SubtitleSafeRegion,
    figure_region,
    paper_figure,
    place,
)

# Narration cue durations in seconds; the pipeline fills in the measured values.
CUES = [4.0, 4.0, 4.0, 4.0]


class TemplateScene(Scene):
    """The paper's own figure on screen, with its parts highlighted as the voice explains them."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(SafeTitle("The A2T architecture", font_size=FONT["title"]), "title")
        self.add(title)

        # Cue 1: the original figure fills the main region, with its label as a badge.
        figure = paper_figure("figure-1.png", "main")
        badge = Text("Figure 1", font_size=FONT["note"], color=GREY_B)
        badge.next_to(figure, DOWN, buff=0.15).align_to(figure, RIGHT)
        clock.play(FadeIn(figure), FadeIn(badge), run_time=1.0)
        clock.next_cue()

        # Cue 2: point at the part the narration names. Fractions are measured
        # from the figure's top-left corner, read off the actual image.
        part_a = figure_region(figure, 0.02, 0.05, 0.52, 0.92)
        clock.play(Create(part_a), run_time=0.8)
        clock.next_cue()

        # Cue 3: move the highlight to the next part; dim the rest instead of covering it.
        part_b = figure_region(figure, 0.55, 0.05, 0.98, 0.92, color=TEAL)
        clock.play(ReplacementTransform(part_a, part_b), run_time=0.8)
        clock.next_cue()

        # Cue 4: move the figure into the left panel (labels are re-placed at
        # full size, never shrunk) and state the takeaway in the right panel.
        figure_group = Group(figure, part_b)
        x, y, width, height = REGIONS["left"]
        factor = min(width / figure_group.width, height / figure_group.height)
        clock.play(
            figure_group.animate.scale(factor).move_to([x, y, 0]), FadeOut(badge), run_time=1.0
        )
        badge.next_to(figure, DOWN, buff=0.15).align_to(figure, RIGHT)
        clock.play(FadeIn(badge), run_time=0.4)
        takeaway = MathTex(r"K_T = w_B K_B + \sum_i w_i K_i", font_size=FONT["equation"])
        place(takeaway, "right")
        clock.play(Write(takeaway), run_time=1.0)
        clock.finish()
