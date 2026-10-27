from manim import *

from scholarmotion.manim_runtime.components import (
    FONT,
    CueClock,
    SafeTitle,
    SubtitleSafeRegion,
    place,
)

# Narration cue durations in seconds; the pipeline fills in the measured values.
CUES = [4.0, 5.0, 5.0, 4.0]


class TemplateScene(Scene):
    """A window sweeping over a grid of values (convolution, attention over tokens, arrays)."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(
            SafeTitle("Sliding a window over the input", font_size=FONT["title"]), "title"
        )
        self.add(title)

        values = [[1, 0, 2, 1], [0, 3, 1, 0], [2, 1, 0, 1], [1, 2, 1, 0]]

        def cell(value, color):
            square = Square(0.8, color=color, stroke_width=2)
            return VGroup(square, Text(str(value), font_size=FONT["note"]).move_to(square))

        def grid(rows, color):
            # Relative layout: arrange_in_grid spaces the cells by their real
            # sizes, so no cell position is computed by hand.
            cells = VGroup(*[cell(value, color) for row in rows for value in row])
            return cells.arrange_in_grid(rows=len(rows), cols=len(rows[0]), buff=0)

        # Cue 1: input grid on the left with its label above.
        image = grid(values, BLUE_B)
        image_label = Text("input", font_size=FONT["label"]).next_to(image, UP, buff=0.25)
        left = place(VGroup(image, image_label), "left")
        clock.play(FadeIn(left), run_time=1.0)
        clock.next_cue()

        # Cue 2: output placeholders on the right; one persistent window.
        output = grid([["?", "?"], ["?", "?"]], GREEN_B)
        output_label = Text("output", font_size=FONT["label"]).next_to(output, UP, buff=0.25)
        right = place(VGroup(output, output_label), "right")
        window = SurroundingRectangle(VGroup(*image[0:2], *image[4:6]), color=YELLOW, buff=0.02)
        clock.play(FadeIn(right), Create(window), run_time=1.0)
        clock.next_cue()

        # Cue 3: move the same window; each result replaces its placeholder in place.
        positions = [(0, 0), (0, 2), (2, 0), (2, 2)]
        for index, (r, c) in enumerate(positions):
            covered = VGroup(*[image[(r + dr) * 4 + c + dc] for dr in (0, 1) for dc in (0, 1)])
            total = sum(values[r + dr][c + dc] for dr in (0, 1) for dc in (0, 1))
            new_value = Text(str(total), font_size=FONT["note"]).move_to(output[index][1])
            clock.play(
                window.animate.move_to(covered),
                ReplacementTransform(output[index][1], new_value),
                run_time=0.6,
            )
        clock.next_cue()

        # Cue 4: remove the tool, keep the finished result on screen.
        rule = place(
            Text("each output = sum of a 2 x 2 window", font_size=FONT["note"]), "equation"
        )
        clock.play(FadeOut(window), Write(rule), run_time=1.0)
        clock.finish()
