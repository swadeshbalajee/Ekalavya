from manim import *

from scholarmotion.manim_runtime.components import (
    FONT,
    CueClock,
    SafeTitle,
    SubtitleSafeRegion,
    arrange_in_band,
    place,
)

# Narration cue durations in seconds; the pipeline fills in the measured values.
CUES = [4.0, 4.0, 4.0, 4.0]


class TemplateScene(Scene):
    """Matrix operation shown step by step: operand, highlight, combine, result."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(SafeTitle("Adding a mask to the scores", font_size=FONT["title"]), "title")
        self.add(title)

        def labeled(name, rows, color):
            matrix = Matrix(rows, element_to_mobject_config={"font_size": FONT["value"]})
            matrix.set_color(color)
            label = MathTex(name, font_size=FONT["equation"], color=color)
            label.next_to(matrix, LEFT, buff=0.3)
            return VGroup(label, matrix)

        # Cue 1: one operand, alone and large in the main region.
        scores = labeled("S", [["4", "3", "2"], ["1", "5", "2"], ["3", "1", "4"]], BLUE_B)
        place(scores, "main")
        clock.play(FadeIn(scores), run_time=1.0)
        clock.next_cue()

        # Cue 2: point at exactly what the narration names; equation in its own band.
        row = VGroup(*scores[1].get_rows()[1])
        highlight = SurroundingRectangle(row, color=YELLOW, buff=0.1)
        rule = place(MathTex(r"A = S + M", font_size=FONT["equation"]), "equation")
        clock.play(Create(highlight), Write(rule), run_time=1.0)
        clock.next_cue()

        # Cue 3: combine operands in one band; the old copy leaves in the same play.
        mask = labeled(
            "M", [["0", r"-\infty", r"-\infty"], ["0", "0", r"-\infty"], ["0", "0", "0"]], RED_B
        )
        plus = MathTex("+", font_size=FONT["equation"])
        moved_scores = scores.copy()
        combined = arrange_in_band(moved_scores, plus, mask)
        place(combined, "main")
        clock.play(FadeOut(highlight), ReplacementTransform(scores, moved_scores), run_time=0.8)
        clock.play(FadeIn(plus), FadeIn(mask), run_time=0.8)
        clock.next_cue()

        # Cue 4: the result replaces the operands and stays until the narration ends.
        result = labeled(
            "A", [["4", r"-\infty", r"-\infty"], ["1", "5", r"-\infty"], ["3", "1", "4"]], GREEN_B
        )
        place(result, "main")
        clock.play(ReplacementTransform(combined, result), run_time=1.0)
        clock.finish()
