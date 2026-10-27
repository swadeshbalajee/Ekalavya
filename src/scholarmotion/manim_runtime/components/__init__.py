"""Layout regions, cue timing, and figure helpers imported by generated scenes."""

from __future__ import annotations

from scholarmotion.media.math_notation import canonical_to_tex

try:
    from manim import (
        BLUE,
        DOWN,
        WHITE,
        YELLOW,
        Arrow,
        Axes,
        ImageMobject,
        MathTex,
        Rectangle,
        RoundedRectangle,
        Text,
        VGroup,
    )
except ImportError as exc:  # pragma: no cover - exercised in media deployment
    raise RuntimeError("Manim components require the 'manim' optional dependency") from exc


# Fixed screen regions (center_x, center_y, width, height) on the 14.22 x 8 frame.
# Subtitles own everything below y=-2.8; the verifier enforces these bands.
REGIONS = {
    "title": (0.0, 3.25, 12.5, 0.7),
    "main": (0.0, 0.32, 11.0, 4.2),
    "left": (-3.0, 0.32, 5.2, 4.2),
    "right": (3.0, 0.32, 5.2, 4.2),
    # 0.8 tall so a fraction at FONT["equation"] fits without shrinking below
    # the readable minimum; spans y=-2.65..-1.85, clear of main and subtitles.
    "equation": (0.0, -2.25, 11.0, 0.8),
}
# Font sizes per role. Matrix/grid values at FONT["value"] stay above the
# verifier's minimum readable height without any extra scaling.
FONT = {"title": 36, "label": 30, "value": 48, "equation": 40, "note": 28}


def place(mobject, region: str = "main"):
    """Shrink (never enlarge) a mobject to fit a named region and center it there."""
    x, y, width, height = REGIONS[region]
    fit_to_region(mobject, width, height)
    mobject.move_to([x, y, 0])
    return mobject


class CueClock:
    """Keep animation in step with narration cues.

    Each visual change starts at the beginning of its spoken cue; ``next_cue``
    holds the picture until that cue ends. Total scene length equals the sum
    of the cue durations, so the scene ends exactly when the narration does.
    """

    def __init__(self, scene, cue_durations):
        self.scene = scene
        self.cues = [max(0.2, float(value)) for value in cue_durations] or [1.0]
        self.index = 0
        self.used = 0.0

    @property
    def remaining(self) -> float:
        if self.index >= len(self.cues):
            return 0.0
        return max(0.0, self.cues[self.index] - self.used)

    def play(self, *animations, run_time: float = 1.0, **kwargs):
        """Play within the current cue; long animations are shortened to fit it."""
        budget = self.remaining
        duration = max(0.25, min(run_time, budget - 0.05)) if budget > 0.3 else 0.25
        self.scene.play(*animations, run_time=duration, **kwargs)
        self.used += duration

    def next_cue(self):
        """Hold the current picture until this cue's narration ends."""
        if self.index >= len(self.cues):
            return
        hold = self.cues[self.index] - self.used
        if hold > 0.02:
            self.scene.wait(hold)
            self.used = 0.0
        else:
            self.used = -hold  # carry a small overrun into the next cue
        self.index += 1

    def finish(self):
        """Hold through every remaining cue so the scene ends with the narration."""
        while self.index < len(self.cues):
            self.next_cue()


def paper_figure(filename: str, region: str = "main"):
    """One of the paper's own figures (copied into ``assets/``), sized to fill a region."""
    image = ImageMobject(f"assets/{filename}")
    x, y, width, height = REGIONS[region]
    image.scale(min(width / image.width, height / image.height))
    image.move_to([x, y, 0])
    return image


def figure_point(image, fx: float, fy: float):
    """Scene point at a fraction of a figure's width/height, measured from its top-left."""
    left, top = image.get_left()[0], image.get_top()[1]
    return [left + fx * image.width, top - fy * image.height, 0]


def figure_region(image, x0: float, y0: float, x1: float, y1: float, color=YELLOW, buff=0.06):
    """A highlight box over part of a figure; fractions from its top-left corner."""
    box = Rectangle(
        width=abs(x1 - x0) * image.width + 2 * buff,
        height=abs(y1 - y0) * image.height + 2 * buff,
        color=color,
        stroke_width=5,
    )
    box.move_to(figure_point(image, (x0 + x1) / 2, (y0 + y1) / 2))
    return box


def fit_to_region(mobject, width: float, height: float):
    if mobject.width > width:
        mobject.scale_to_fit_width(width)
    if mobject.height > height:
        mobject.scale_to_fit_height(height)
    return mobject


def keep_inside_frame(
    mobject,
    *,
    width: float = 13.6,
    height: float = 7.4,
    margin: float = 0.25,
    subtitle_top: float = -3.0,
):
    bottom = max(-height / 2 + margin, subtitle_top + margin)
    top = height / 2 - margin
    fit_to_region(mobject, width - 2 * margin, top - bottom)
    mobject.set_x(
        min(
            max(mobject.get_x(), -width / 2 + mobject.width / 2 + margin),
            width / 2 - mobject.width / 2 - margin,
        )
    )
    mobject.set_y(min(max(mobject.get_y(), bottom + mobject.height / 2), top - mobject.height / 2))
    return mobject


def arrange_in_band(
    *mobjects,
    direction=None,
    buff: float = 0.5,
    max_width: float = 11.0,
    max_height: float = 4.2,
    min_scale: float = 0.7,
):
    """Lay out related objects (e.g. ``S``, ``+``, ``M``) without collisions.

    Arranges them in a row, shrinking at most to ``min_scale`` so text stays
    readable; if the row still does not fit, stacks them vertically instead.
    Returns the VGroup, centred; position it afterwards with ``move_to``.
    """
    row = VGroup(*mobjects).arrange(direction if direction is not None else [1, 0, 0], buff=buff)
    scale = min(1.0, max_width / max(row.width, 1e-6), max_height / max(row.height, 1e-6))
    if scale >= min_scale:
        return row.scale(scale) if scale < 1 else row
    column = VGroup(*mobjects).arrange(DOWN, buff=buff * 0.6)
    fit_to_region(column, max_width, max_height)
    return column


def avoid_overlap(mobject, obstacle=None, *, direction=None, buffer: float = 0.25, buff=None):
    if buff is not None:
        buffer = buff
    if obstacle is None:
        items = list(mobject)
        for index, item in enumerate(items):
            for earlier in items[:index]:
                avoid_overlap(item, earlier, direction=direction, buffer=buffer)
        return items
    if (
        mobject.get_left()[0] < obstacle.get_right()[0]
        and mobject.get_right()[0] > obstacle.get_left()[0]
    ):
        mobject.next_to(obstacle, direction if direction is not None else DOWN, buff=buffer)
    return keep_inside_frame(mobject)


class SafeTitle(VGroup):
    def __init__(self, text: str, *, subtitle: str | None = None, **kwargs):
        font_size = kwargs.pop("font_size", 40)
        color = kwargs.pop("color", WHITE)
        title_text = Text(text, font_size=font_size, color=color, **kwargs)
        if subtitle:
            subtitle_text = Text(subtitle, font_size=max(18, round(font_size * 0.5)), color=color)
            super().__init__(title_text, subtitle_text)
            self.arrange(DOWN, buff=0.15)
        else:
            super().__init__(title_text)
        self.to_edge([0, 1, 0], buff=0.25)
        fit_to_region(self, 12.5, 1.2 if subtitle else 0.7)


class EquationPanel(VGroup):
    def __init__(
        self,
        equation=None,
        *,
        equations=None,
        title: str | None = None,
        width: float | None = None,
        height: float | None = None,
        **kwargs,
    ):
        entries = equations if equations else ([equation] if equation is not None else [])
        if not entries:
            raise ValueError("EquationPanel requires 'equation' or 'equations'")
        tex_group = VGroup(
            *[entry if not isinstance(entry, str) else MathTex(entry) for entry in entries]
        ).arrange(DOWN, buff=0.35)
        fit_to_region(tex_group, 5.5, 2.0)
        content = (
            VGroup(Text(title, font_size=24, color=WHITE), tex_group).arrange(DOWN, buff=0.2)
            if title
            else tex_group
        )
        panel = RoundedRectangle(
            width=max(2, content.width + 0.5), height=max(1, content.height + 0.4), color=BLUE
        )
        super().__init__(panel, content, **kwargs)
        if width is not None:
            self.scale_to_fit_width(width)
        if height is not None:
            self.scale_to_fit_height(height)


class STEMEquation(VGroup):
    """Typeset a canonical formula through TeX; never substitute missing glyphs."""

    def __init__(self, notation: str, font_size: float = 30, **kwargs):
        self.notation = notation
        super().__init__(MathTex(canonical_to_tex(notation), font_size=font_size, **kwargs))


class LabeledAxes(VGroup):
    def __init__(self, x_label: str = "x", y_label: str = "y", **kwargs):
        axes = Axes(**kwargs)
        super().__init__(axes, axes.get_x_axis_label(x_label), axes.get_y_axis_label(y_label))


class StepEquationTransform(VGroup):
    def __init__(self, *equations: str):
        super().__init__(*[MathTex(eq) for eq in equations])
        self.arrange(DOWN, buff=0.35)


class VectorArrow(Arrow):
    def __init__(self, start, end, label: str | None = None, **kwargs):
        super().__init__(start, end, **kwargs)
        self.vector_label = MathTex(label).next_to(self, [0, 1, 0]) if label else None


class ComparisonTable(VGroup):
    def __init__(self, rows: list[tuple[str, str]], **kwargs):
        entries = [
            VGroup(Text(left, font_size=26), Text(right, font_size=26)).arrange([1, 0, 0], buff=0.5)
            for left, right in rows
        ]
        super().__init__(*entries, **kwargs)
        self.arrange(DOWN, aligned_edge=[-1, 0, 0])


class PaperFigurePanel(VGroup):
    def __init__(self, path: str, caption: str, **kwargs):
        image = ImageMobject(path)
        fit_to_region(image, 6.0, 4.2)
        label = Text(caption, font_size=22)
        super().__init__(image, label, **kwargs)
        self.arrange(DOWN)


def mark_reserved(mobject, region: str = "subtitles"):
    """Tag a mobject as occupying a named layout region, exempting it from overlap checks
    against other objects sharing the same explicit region (see verify_layout)."""
    mobject.reserved_region = region
    return mobject


class SubtitleSafeRegion(Rectangle):
    def __init__(self, **kwargs):
        super().__init__(
            width=13.5,
            height=1.0,
            color=kwargs.pop("color", BLUE),
            stroke_opacity=kwargs.pop("stroke_opacity", 0.0),
            **kwargs,
        )
        self.to_edge(DOWN, buff=0)
        # This rectangle *is* the subtitle region: it is pinned to the bottom
        # edge, so it always sits below the subtitle-safe boundary. Tagging it
        # on construction keeps verify_layout from reporting the marker as an
        # intruder into the very area it defines.
        self.reserved_region = "subtitles"

    def reserve(self, region: str = "subtitles"):
        return mark_reserved(self, region)


class HighlightBox(RoundedRectangle):
    def __init__(self, mobject, **kwargs):
        super().__init__(
            width=mobject.width + 0.25,
            height=mobject.height + 0.2,
            color=kwargs.pop("color", YELLOW),
            **kwargs,
        )
        self.move_to(mobject)


class TimelineLabel(Text):
    def __init__(self, text: str, **kwargs):
        super().__init__(text, font_size=kwargs.pop("font_size", 24), **kwargs)
