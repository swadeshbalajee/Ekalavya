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
    """A physical setup with labelled parts, a quantity that changes, and its law."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(SafeTitle("Torque on a wrench", font_size=FONT["title"]), "title")
        self.add(title)

        # Cue 1: the setup, built as one group and placed in the main region.
        # Build relative to one object (the bar); place the whole group once.
        bar = Line(ORIGIN, RIGHT * 4, stroke_width=10, color=GREY_B)
        pivot = Dot(bar.get_start(), color=WHITE)
        pivot_label = Text("pivot", font_size=FONT["note"]).next_to(
            pivot, DOWN, buff=MED_SMALL_BUFF
        )
        setup = VGroup(bar, pivot, pivot_label)
        place(setup, "main")
        clock.play(Create(bar), FadeIn(pivot, pivot_label), run_time=1.2)
        clock.next_cue()

        # Cue 2: a force arrow whose label sits beside it, never on top of the line.
        angle = ValueTracker(90)

        def force_arrow():
            tip = bar.get_end()
            direction = np.array(
                [np.cos(angle.get_value() * DEGREES), np.sin(angle.get_value() * DEGREES), 0]
            )
            return Arrow(tip, tip + 1.6 * direction, buff=0, color=YELLOW)

        force = always_redraw(force_arrow)
        force_label = always_redraw(
            lambda: MathTex("F", font_size=FONT["label"], color=YELLOW).next_to(
                force, RIGHT, buff=0.2
            )
        )
        clock.play(FadeIn(force), FadeIn(force_label), run_time=1.0)
        clock.next_cue()

        # Cue 3: change one quantity continuously while the narration explains the effect.
        law = place(MathTex(r"\tau = r F \sin\theta", font_size=FONT["equation"]), "equation")
        clock.play(Write(law), run_time=0.8)
        clock.play(angle.animate.set_value(30), run_time=2.0)
        clock.next_cue()

        # Cue 4: return to the best case and hold the finished picture.
        clock.play(angle.animate.set_value(90), Indicate(law, scale_factor=1.1), run_time=1.5)
        clock.finish()
