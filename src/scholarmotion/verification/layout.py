"""Detects overlap and out-of-frame objects from captured bounds."""

from __future__ import annotations

from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.schemas import VerificationIssue

_TEXT_OBJECTS = {
    "Text",
    "SafeTitle",
    "MathTex",
    "Tex",
    "MarkupText",
    "TimelineLabel",
    "STEMEquation",
}
_SHAPE_OBJECTS = {
    "Polygon",
    "Rectangle",
    "RoundedRectangle",
    "Circle",
    "Ellipse",
    "Square",
    "Triangle",
    "DashedVMobject",
    "Axes",
    "NumberPlane",
}
# Annotations Manim draws on top of their target by design (box, strike-out,
# underline, highlight backdrop); covering the target is their purpose.
_INTENTIONAL_OVERLAYS = {"SurroundingRectangle", "Cross", "Underline", "BackgroundRectangle"}
_MARKER_OBJECTS = {"Dot"}
_DIAGRAM_CONNECTORS = {
    "Arrow",
    "Arc",
    "Line",
    "DashedLine",
    "DoubleArrow",
    "Vector",
    "ParametricFunction",
    "FunctionGraph",
}
_CIRCULAR_CONTOURS = {"Circle", "Ellipse", "DashedVMobject"}


def _kind(name: str) -> str:
    return name.rsplit("_", 1)[0]


def is_text(obj: ObjectBounds) -> bool:
    return _kind(obj.name) in _TEXT_OBJECTS


def _contains(outer: ObjectBounds, inner: ObjectBounds, *, tolerance: float = 0.03) -> bool:
    return (
        outer.left - tolerance <= inner.left
        and outer.right + tolerance >= inner.right
        and outer.bottom - tolerance <= inner.bottom
        and outer.top + tolerance >= inner.top
    )


def verify_layout(
    scene_id: str,
    objects: list[ObjectBounds],
    *,
    frame=(-7.0, 7.0, -4.0, 4.0),
    subtitle_top: float = -3.0,
    min_text_height: float = 0.22,
    min_font_size: float = 24.0,
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    left, right, bottom, top = frame
    for obj in objects:
        if obj.left < left or obj.right > right or obj.bottom < bottom or obj.top > top:
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.out_of_frame",
                    severity="high",
                    confidence=1,
                    objects=[obj.name],
                    description=f"{obj.name} leaves the safe frame.",
                    suggested_repair=(
                        f"The {obj.name} object (bounds x=[{obj.left:.2f}, {obj.right:.2f}], "
                        f"y=[{obj.bottom:.2f}, {obj.top:.2f}]) leaves the safe frame "
                        f"(x in [{left}, {right}], y in [{bottom}, {top}]). Reposition it inside "
                        "the frame and call keep_inside_frame() on it, or scale it down."
                    ),
                    correction_memory_candidate=True,
                )
            )
        # Prefer the effective font size: glyph height misjudges short symbols
        # such as a lone lowercase "v", while font size reflects real scaling.
        too_small = (
            obj.font_size < min_font_size
            if obj.font_size is not None
            else obj.text_height is not None and obj.text_height < min_text_height
        )
        if too_small:
            measured = (
                f"effective font_size {obj.font_size:.0f} (minimum {min_font_size:.0f})"
                if obj.font_size is not None
                else f"height {obj.text_height:.3f} (minimum {min_text_height})"
            )
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.text_too_small",
                    severity="medium",
                    confidence=1,
                    objects=[obj.name],
                    description=f"{obj.name} text is too small: {measured}.",
                    suggested_repair=(
                        f"The {obj.name} text ({obj.label or 'text'!r}) renders at {measured}. "
                        "Do not shrink it with scale()/fit; give it a larger font_size or show "
                        "fewer objects at once so it fits without shrinking."
                    ),
                    correction_memory_candidate=True,
                )
            )
        if obj.reserved_region != "subtitles" and obj.bottom < subtitle_top:
            issues.append(
                VerificationIssue(
                    scene_id=scene_id,
                    category="layout.subtitle_collision",
                    severity="high",
                    confidence=0.98,
                    objects=[obj.name],
                    description=f"{obj.name} enters the subtitle safe area.",
                    suggested_repair=(
                        f"The {obj.name} object extends down to y={obj.bottom:.2f}, below the "
                        f"subtitle-safe boundary y={subtitle_top}. Move it up so it stays above "
                        "SubtitleSafeRegion."
                    ),
                    correction_memory_candidate=True,
                )
            )
    for index, first in enumerate(objects):
        for second in objects[index + 1 :]:
            same_explicit_region = (
                first.reserved_region is not None
                and first.reserved_region == second.reserved_region
            )
            first_kind, second_kind = _kind(first.name), _kind(second.name)
            intentional_container_label = (
                first_kind in _TEXT_OBJECTS
                and (second_kind in _SHAPE_OBJECTS | _MARKER_OBJECTS)
                and _contains(second, first)
            ) or (
                second_kind in _TEXT_OBJECTS
                and first_kind in _SHAPE_OBJECTS | _MARKER_OBJECTS
                and _contains(first, second)
            )
            intentional_contained_marker = (
                first_kind in _MARKER_OBJECTS
                and second_kind in _SHAPE_OBJECTS
                and _contains(second, first)
            ) or (
                second_kind in _MARKER_OBJECTS
                and first_kind in _SHAPE_OBJECTS
                and _contains(first, second)
            )
            intentional_nested_shapes = (
                first_kind in _CIRCULAR_CONTOURS
                and second_kind in _CIRCULAR_CONTOURS
                and (_contains(first, second) or _contains(second, first))
            )
            field_arrow_crossing = (first_kind == "Arrow" and second_kind in _SHAPE_OBJECTS) or (
                second_kind == "Arrow" and first_kind in _SHAPE_OBJECTS
            )
            intentional_overlay = (
                first_kind in _INTENTIONAL_OVERLAYS
                or second_kind in _INTENTIONAL_OVERLAYS
                # Boxes, arrows and markers drawn on a paper's figure annotate it.
                or (
                    "ImageMobject" in {first_kind, second_kind}
                    and not ({first_kind, second_kind} & _TEXT_OBJECTS)
                )
            )
            diagram_connector_crossing = (
                first_kind in _DIAGRAM_CONNECTORS and second_kind not in _TEXT_OBJECTS
            ) or (second_kind in _DIAGRAM_CONNECTORS and first_kind not in _TEXT_OBJECTS)
            text_collision = first_kind in _TEXT_OBJECTS or second_kind in _TEXT_OBJECTS
            meaningful_overlap = first.overlaps(
                second, min_overlap_fraction=0.15 if text_collision else 0.7
            )
            # A line or curve's bounding box is mostly empty space; when its path
            # was sampled, text collides only if the stroke passes through it.
            if text_collision and first.points and second_kind in _TEXT_OBJECTS:
                meaningful_overlap = second.crossed_by(first)
            elif text_collision and second.points and first_kind in _TEXT_OBJECTS:
                meaningful_overlap = first.crossed_by(second)
            if (
                meaningful_overlap
                and not same_explicit_region
                and not intentional_container_label
                and not intentional_contained_marker
                and not intentional_nested_shapes
                and not field_arrow_crossing
                and not intentional_overlay
                and not diagram_connector_crossing
            ):
                issues.append(
                    VerificationIssue(
                        scene_id=scene_id,
                        category="layout.overlap",
                        severity="high",
                        confidence=0.97,
                        objects=[first.name, second.name],
                        description=f"{first.name} overlaps {second.name}.",
                        suggested_repair=(
                            f"The {first.name} ({first.label or first_kind!r}, "
                            f"x=[{first.left:.2f},{first.right:.2f}], y=[{first.bottom:.2f},{first.top:.2f}]) "
                            f"overlaps {second.name} ({second.label or second_kind!r}, "
                            f"x=[{second.left:.2f},{second.right:.2f}], y=[{second.bottom:.2f},{second.top:.2f}]). "
                            "Reserve non-overlapping layout regions before scaling, and call "
                            "avoid_overlap(first, second) between them."
                        ),
                        correction_memory_candidate=True,
                    )
                )
    return issues
