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

# Values copied exactly from the paper's table; never invent or round them differently.
CATEGORIES = ["Baseline A", "Baseline B", "Proposed"]
VALUES = [25.8, 26.4, 28.4]
Y_MAX, Y_STEP = 30, 10
SOURCE = "Table 2"


class TemplateScene(Scene):
    """A results plot from a paper: bars grow, values appear, the key comparison is highlighted."""

    def construct(self):
        clock = CueClock(self, CUES)
        self.add(SubtitleSafeRegion())
        title = place(SafeTitle("Translation quality (BLEU)", font_size=FONT["title"]), "title")
        self.add(title)

        # Cue 1: axes and bars, built as one group and placed once.
        chart = BarChart(
            VALUES,
            y_range=[0, Y_MAX, Y_STEP],
            x_length=6.4,
            y_length=3.2,
            bar_colors=[GREY_B, GREY_B, YELLOW],
            bar_width=0.55,
            y_axis_config={"font_size": FONT["note"]},
        )
        names = VGroup(
            *(
                Text(name, font_size=FONT["note"]).next_to(bar, DOWN, buff=0.2)
                for name, bar in zip(CATEGORIES, chart.bars)
            )
        )
        source = Text(SOURCE, font_size=FONT["note"], color=GREY_B)
        plot = VGroup(chart, names)
        source.next_to(plot, RIGHT, buff=0.3).align_to(plot, UP)
        place(VGroup(plot, source), "main")
        clock.play(Create(chart.axes), FadeIn(names, source), run_time=1.0)
        clock.play(*(GrowFromEdge(bar, DOWN) for bar in chart.bars), run_time=1.2)
        clock.next_cue()

        # Cue 2: exact values from the table, attached to their bars.
        labels = chart.get_bar_labels(font_size=FONT["note"])
        clock.play(FadeIn(labels), run_time=0.8)
        clock.next_cue()

        # Cue 3: highlight the comparison the narration discusses.
        best = VGroup(chart.bars[-1], labels[-1])
        highlight = SurroundingRectangle(best, color=YELLOW, buff=0.1)
        clock.play(Create(highlight), chart.bars[:-1].animate.set_opacity(0.35), run_time=1.0)
        clock.next_cue()

        # Cue 4: the quantitative takeaway, held until the narration ends.
        gain = MathTex(r"28.4 - 26.4 = +2.0\ \text{BLEU}", font_size=FONT["equation"])
        place(gain, "equation")
        clock.play(Write(gain), run_time=1.0)
        clock.finish()
