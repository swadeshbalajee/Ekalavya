from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.verification.layout import verify_layout


def test_layout_verifier_allows_field_arrows_to_cross_a_surface():
    surface = ObjectBounds("Polygon_1", -1, 1, -1, 1)
    arrow = ObjectBounds("Arrow_2", -0.05, 0.05, -1.5, 1.5)

    assert not [
        issue
        for issue in verify_layout("S01", [surface, arrow])
        if issue.category == "layout.overlap"
    ]


def test_layout_verifier_allows_intentional_label_inside_shape_but_flags_label_on_arrow():
    surface = ObjectBounds("Rectangle_1", -2, 2, -2, 2)
    label = ObjectBounds("Text_2", -0.2, 0.2, -0.2, 0.2, text_height=0.4)
    arrow = ObjectBounds("Arrow_3", -0.05, 0.05, -1, 1)

    issues = verify_layout("S01", [surface, label, arrow])

    assert all(set(issue.objects) != {"Rectangle_1", "Text_2"} for issue in issues)
    assert any(set(issue.objects) == {"Text_2", "Arrow_3"} for issue in issues)


def test_layout_verifier_allows_highlight_frames_and_diagram_connectors():
    surface = ObjectBounds("Polygon_1", -1, 1, -1, 1)
    highlight = ObjectBounds("SurroundingRectangle_2", -0.4, 0.4, -0.4, 0.4)
    arc = ObjectBounds("Arc_3", -0.5, 0.5, -0.5, 0.5)
    arrow = ObjectBounds("Arrow_4", -0.5, 0.5, -0.5, 0.5)

    issues = verify_layout("S01", [surface, highlight, arc, arrow])

    assert not [issue for issue in issues if issue.category == "layout.overlap"]


def test_layout_verifier_allows_small_particle_contained_inside_diagram_shape():
    pipe = ObjectBounds("Polygon_1", -4.2, 4.2, 1.15, 2.3)
    water_particle = ObjectBounds("Dot_2", -3.2, -3.0, 1.6, 1.8)

    issues = verify_layout("S01", [pipe, water_particle])

    assert not [issue for issue in issues if issue.category == "layout.overlap"]


def test_layout_verifier_allows_charge_and_gaussian_surface_nesting():
    surface = ObjectBounds("Circle_1", -1.4, 1.4, -1.4, 1.4)
    charge = ObjectBounds("Circle_2", -0.4, 0.4, -0.4, 0.4)
    label = ObjectBounds("Text_3", -0.2, 0.2, -0.15, 0.15, text_height=0.3)
    dashed_surface = ObjectBounds("DashedVMobject_4", -1.4, 1.4, -1.4, 1.4)
    point_charge = ObjectBounds("Dot_5", 0.75, 0.85, 0.75, 0.85)

    issues = verify_layout("S01", [surface, charge, label, dashed_surface, point_charge])

    assert not [issue for issue in issues if issue.category == "layout.overlap"]


def test_layout_verifier_ignores_small_overlaps_between_assembled_diagram_shapes():
    pipe = ObjectBounds("Rectangle_1", -2, 2, -0.5, 0.5)
    cap = ObjectBounds("Rectangle_2", -2.2, -1.8, -0.5, 0.5)
    occluding_panel = ObjectBounds("Rectangle_3", -1.9, 1.9, -0.45, 0.45)

    issues = verify_layout("S01", [pipe, cap, occluding_panel])
    collisions = [issue for issue in issues if issue.category == "layout.overlap"]

    assert len(collisions) == 1
    assert set(collisions[0].objects) == {"Rectangle_1", "Rectangle_3"}
