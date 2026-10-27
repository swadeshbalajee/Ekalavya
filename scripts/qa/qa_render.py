"""Exercise the real Windows renderer without model calls."""

import json
from dataclasses import asdict
from pathlib import Path

from scholarmotion.manim_runtime.sandbox import SandboxRenderer

code = """from manim import *
class Probe(Scene):
    def construct(self):
        self.add(Text("Windows render"))
        self.wait(1)
"""
result = SandboxRenderer(mock_missing=False).render(code, "Probe", ".local/qa/render-probe")
Path(".local/qa/render-probe-result.json").write_text(
    json.dumps(asdict(result), indent=2), encoding="utf-8"
)
print(json.dumps(asdict(result), indent=2))
