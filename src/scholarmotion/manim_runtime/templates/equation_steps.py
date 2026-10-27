from manim import *

from scholarmotion.manim_runtime.components import (
    FONT,
    CueClock,
    SafeTitle,
    SubtitleSafeRegion,
    place,
)

# Narration cue durations in seconds; the pipeline fills in the measured values.
CUES = [4.0, 4.0, 4.0, 4.0]


class TemplateScene(Scene):
    """A formula built up step by step beside a small plot that shows what it means."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(
            SafeTitle("Kinetic energy grows with speed", font_size=FONT["title"]), "title"
        )
        self.add(title)

        # Cue 1: the plot owns the left panel for the whole scene.
        axes = Axes(x_range=[0, 4, 1], y_range=[0, 8, 2], x_length=4.6, y_length=3.4, tips=False)
        x_label = MathTex("v", font_size=FONT["label"]).next_to(axes.x_axis, RIGHT, buff=0.2)
        y_label = MathTex("E_k", font_size=FONT["label"]).next_to(axes.y_axis, UP, buff=0.2)
        place(VGroup(axes, x_label, y_label), "left")
        clock.play(Create(axes), FadeIn(x_label, y_label), run_time=1.2)
        clock.next_cue()

        # Cue 2: the formula appears in the right panel.
        formula = place(MathTex(r"E_k = \tfrac{1}{2} m v^{2}", font_size=FONT["equation"]), "right")
        curve = axes.plot(lambda v: 0.5 * v**2, x_range=[0, 4], color=YELLOW)
        clock.play(Write(formula), Create(curve), run_time=1.2)
        clock.next_cue()

        # Cue 3: substitute numbers; the old line is transformed, never stacked.
        worked = MathTex(r"E_k = \tfrac{1}{2}(2)(3)^{2} = 9", font_size=FONT["equation"])
        worked.move_to(formula)
        point = Dot(axes.c2p(3, 4.5), color=RED)
        # Below-right of the point stays clear of a rising curve.
        point_label = MathTex("v=3", font_size=FONT["note"]).next_to(point, DR, buff=0.15)
        clock.play(TransformMatchingTex(formula, worked), FadeIn(point, point_label), run_time=1.2)
        clock.next_cue()

        # Cue 4: takeaway in the equation band; everything stays until the narration ends.
        takeaway = place(
            Text("double the speed, four times the energy", font_size=FONT["note"]), "equation"
        )
        clock.play(Write(takeaway), run_time=1.0)
        clock.finish()
