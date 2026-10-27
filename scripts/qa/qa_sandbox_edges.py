"""Safe negative code/storage checks and a bounded renderer timeout probe."""

import json
from dataclasses import asdict
from pathlib import Path

from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.manim_runtime.validator import validate_generated_code
from scholarmotion.persistence.storage import LocalObjectStore

root = Path(".local/qa")
checks = []
for label, code in {
    "OS import": 'import os\nos.system("echo blocked")',
    "network import": "import socket\nsocket.socket()",
    "process import": 'import subprocess\nsubprocess.run(["echo", "blocked"])',
    "dynamic import": '__import__("os")',
    "eval": 'eval("1+1")',
    "exec": 'exec("x=1")',
    "file access": 'open("blocked.txt","w")',
    "invalid Python": "class Broken(",
}.items():
    validation = validate_generated_code(code)
    checks.append({"name": label, "passed": not validation.accepted, "errors": validation.errors})
store = LocalObjectStore(root / "negative-store")
for key in ("../escape.txt", "/absolute.txt", "a/../../escape.txt", "C:\\escape.txt"):
    try:
        store.put_bytes(key, b"harmless QA text")
        passed = False
    except ValueError:
        passed = True
    checks.append({"name": "storage key " + key, "passed": passed})
store.put_bytes("immutable.txt", b"original", overwrite=True)
try:
    store.put_bytes("immutable.txt", b"replacement")
    passed = False
except FileExistsError:
    passed = store.get_bytes("immutable.txt") == b"original"
checks.append({"name": "immutable artifact overwrite rejected", "passed": passed})
code = """from manim import *
class SceneTimeout(Scene):
    def construct(self):
        self.add(Text("Bounded timeout probe"))
        self.wait(5)
"""
render = SandboxRenderer(mock_missing=False, timeout_seconds=0.3).render(
    code, "SceneTimeout", root / "timeout-render"
)
checks.append(
    {
        "name": "render timeout bounded",
        "passed": not render.success and render.return_code == 124,
        "render": asdict(render),
    }
)
(root / "sandbox-edge-results.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
print(f"Sandbox/storage checks: {sum(c['passed'] for c in checks)}/{len(checks)}")
