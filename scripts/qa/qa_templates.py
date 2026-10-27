"""Render every Manim template and run the deterministic verifiers on it.

Usage (scholarmotion env, TeX on PATH):  python scripts/qa/qa_templates.py
Templates are what the code model imitates, so each must render, match its
cue timing, and produce zero layout findings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.manim_runtime.templates import TEMPLATES, with_cues
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.services.orchestration import _verify_render_layout
from scholarmotion.verification.render import verify_no_raw_latex_text

OUT = Path(".local/qa/templates")


def main() -> int:
    renderer = SandboxRenderer(mock_missing=False, timeout_seconds=300)
    assembler = FFmpegAssembler(mock_missing=False)
    cues = [3.5, 4.5, 5.0, 3.0]
    failures = 0
    report = []
    only = set(sys.argv[1:])
    for template in TEMPLATES:
        if only and template.name not in only:
            continue
        code = with_cues(template.source, cues)
        # The paper_figure template shows a real cropped figure from assets/.
        assets = Path(".local/qa/paper/figures") if template.name == "paper_figure" else None
        result = renderer.render(code, "TemplateScene", OUT / template.name, assets_dir=assets)
        duration = assembler.probe_duration(result.video_path) if result.video_path else None
        issues = [
            *_verify_render_layout(template.name, result),
            *verify_no_raw_latex_text(template.name, code),
        ]
        ok = result.success and not issues and duration is not None and abs(duration - sum(cues)) < 0.3
        failures += not ok
        row = {
            "template": template.name,
            "rendered": result.success,
            "duration": duration,
            "expected": sum(cues),
            "issues": [f"{i.category}: {i.description}" for i in issues],
            "error": (result.error or "")[-1500:],
            "keyframes": result.keyframe_paths,
        }
        report.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "keyframes"}, indent=1)[:2500])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
