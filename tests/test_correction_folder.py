"""Lessons from failures live in an editable folder per agent and grow from repairs."""

from __future__ import annotations

from scholarmotion.memory.correction_folder import PROMOTE_AFTER, CorrectionFolder


def test_a_new_folder_starts_with_the_bundled_lessons(tmp_path):
    folder = CorrectionFolder(tmp_path / "corrections")
    folder.ensure()

    assert (tmp_path / "corrections/code/CORRECTIONS.md").exists()
    ids = [note.id for note in folder.entries("code")]
    assert {"C001", "C002", "C004"} <= set(ids)
    assert (tmp_path / "corrections/code/checks/C004_point_indexing.py").exists()
    assert (tmp_path / "corrections/storyboard/entries").is_dir()


def test_the_index_is_always_shown_and_details_only_when_they_apply(tmp_path):
    folder = CorrectionFolder(tmp_path)
    chart = folder.context("code", {"data_plot", "bar"})
    plain = folder.context("code", {"diagram"})

    assert "C001" in plain and "C002" in plain  # the index lists every lesson
    assert "DETAILS THAT APPLY HERE" in chart and "next_to(bar, UP" in chart
    assert "DETAILS THAT APPLY HERE" not in plain
    # A failing check's category is enough to pull its lesson in.
    assert "legend or on empty space" in folder.context(
        "code", {"video.checklist.highlight_targets"}
    )


def test_hand_edits_take_effect_without_restarting(tmp_path):
    folder = CorrectionFolder(tmp_path)
    folder.ensure()
    (tmp_path / "code/entries/C900-arrow-colours.md").write_text(
        "---\nid: C900\ntitle: Arrow colours\ntriggers: arrow\n---\nMatch the curve colour.\n",
        encoding="utf-8",
    )
    assert "C900 Arrow colours" in folder.context("code", set())
    assert "Match the curve colour." in folder.context("code", {"arrow"})


def test_checks_find_their_pattern_and_a_broken_check_is_skipped(tmp_path):
    folder = CorrectionFolder(tmp_path)
    folder.ensure()
    (tmp_path / "code/checks/C999_broken.py").write_text("def check(source):\n    1 / 0\n")
    findings = folder.run_checks("code", "line.set_y(bars[0].get_top()[1][1])\nok = 1\n")

    assert findings == [("C004", findings[0][1])]
    assert "Line 1" in findings[0][1]


def test_a_problem_fixed_in_enough_scenes_becomes_a_lesson(tmp_path):
    folder = CorrectionFolder(tmp_path)
    for scene in range(PROMOTE_AFTER - 1):
        assert (
            folder.record(
                "code", "dashed-line-crosses-label", "line on 8.0", "move it", scene=f"p/S{scene}"
            )
            is None
        )
    # The same scene twice counts once.
    assert (
        folder.record("code", "dashed-line-crosses-label", "again", "again", scene="p/S0") is None
    )
    entry = folder.record(
        "code", "dashed-line-crosses-label", "line on 39.8", "end it short", scene="p/S9"
    )

    assert entry is not None and entry.id.startswith("C")
    assert entry.seen == PROMOTE_AFTER
    assert "line on 39.8" in entry.body and "end it short" in entry.body
    assert not (tmp_path / "code/candidates/dashed-line-crosses-label.md").exists()
    assert entry.id in folder.context("code", set())
    # Once it is a lesson, more cases do not create a second one.
    assert folder.record("code", "dashed-line-crosses-label", "x", "y", scene="p/S10") is None
