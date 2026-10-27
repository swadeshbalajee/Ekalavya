"""Verify native Windows rendering in a path with spaces and Unicode escapes."""

import os
import shutil
from pathlib import Path

import pytest

from scholarmotion.manim_runtime.sandbox import SandboxRenderer


@pytest.mark.media
@pytest.mark.skipif(os.name != "nt" or not shutil.which("manim"), reason="Windows Manim required")
def test_real_windows_render_captures_geometry_in_path_with_spaces(tmp_path):
    code = """from manim import *
class WindowsProbe(Scene):
    def construct(self):
        self.add(Text("Windows probe"))
        self.wait(0.2)
"""
    result = SandboxRenderer(mock_missing=False).render(
        code, "WindowsProbe", tmp_path / "Users and unicode path"
    )
    assert result.success, result.error
    assert result.bounds_path and Path(result.bounds_path).is_file()
    assert result.video_path and Path(result.video_path).stat().st_size > 100
