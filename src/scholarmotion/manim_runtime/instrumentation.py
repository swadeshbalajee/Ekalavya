"""Object bounding boxes captured from renders, with the overlap tests used by layout checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ObjectBounds:
    name: str
    left: float
    right: float
    bottom: float
    top: float
    text_height: float | None = None
    reserved_region: str | None = None
    label: str | None = None
    # Effective font size after scaling, when the renderer could read it.
    font_size: float | None = None
    # Sampled (x, y) points along a thin stroke such as a line or plotted curve.
    points: list[list[float]] | None = None

    def crossed_by(self, stroke: ObjectBounds, *, margin: float = 0.03) -> bool:
        """True when a sampled point of ``stroke`` lies inside this object's box."""
        return any(
            self.left - margin <= x <= self.right + margin
            and self.bottom - margin <= y <= self.top + margin
            for x, y in stroke.points or []
        )

    def overlaps(self, other: ObjectBounds, *, min_overlap_fraction: float = 0.15) -> bool:
        """True only if the intersection covers a meaningful share of the smaller object.

        A thin arrow or line crossing through a shape (e.g. a field arrow drawn through a
        coil) is a deliberate, common diagram composition, not a defect — its bounding-box
        area is near zero, so it never crosses the fraction threshold. Two labels or panels
        substantially covering each other does."""
        x_overlap = min(self.right, other.right) - max(self.left, other.left)
        y_overlap = min(self.top, other.top) - max(self.bottom, other.bottom)
        if x_overlap <= 0 or y_overlap <= 0:
            return False
        overlap_area = x_overlap * y_overlap
        self_area = (self.right - self.left) * (self.top - self.bottom)
        other_area = (other.right - other.left) * (other.top - other.bottom)
        smaller_area = min(self_area, other_area)
        if smaller_area <= 1e-9:
            return False
        return (overlap_area / smaller_area) >= min_overlap_fraction

    def as_dict(self) -> dict:
        return asdict(self)
