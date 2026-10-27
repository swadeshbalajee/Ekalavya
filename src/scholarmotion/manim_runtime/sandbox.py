"""Renders generated Manim code in a resource-limited sandbox and captures keyframes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scholarmotion.media.ffmpeg import resolve_ffmpeg

from .validator import validate_generated_code


def _resource_limits(memory_mb: int, cpu_seconds: int, process_limit: int):
    """Return a POSIX pre-exec hook that constrains an untrusted render child."""

    def apply() -> None:
        import resource

        os.setsid()
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        # Linux applies RLIMIT_NPROC to every process owned by this UID, not
        # just this render's process tree. It is therefore opt-in: using it on
        # a developer login can prevent Cairo/Pango/FFmpeg from creating their
        # own required worker threads. Enforce a process count through cgroups
        # or a container for untrusted multi-user rendering.
        if process_limit > 0:
            resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        os.umask(0o077)

    return apply


def _summarize_traceback(output: str) -> str:
    """'IndexError: … | scene.py line 49: <code>' from Manim's boxed traceback.

    Repair prompts and progress lines show only the start of an error, which
    for a rich traceback is box-drawing noise; this puts the cause first.
    """
    exception = next(
        (
            cleaned
            for line in reversed(output.splitlines())
            if re.match(
                r"^[A-Za-z_.]*(?:Error|Exception)\b",
                cleaned := line.strip().strip("│").strip(),
            )
        ),
        None,
    )
    location = None
    index = output.rfind("scene.py")
    if index != -1:
        match = re.search(r">\s*(\d+)\s*│([^\n]*)", output[index : index + 2000])
        if match:
            code = " ".join(match.group(2).replace("│", " ").split())
            location = f"scene.py line {match.group(1)}: {code}"
    return " | ".join(part for part in (exception, location) if part)


def _tail_error(output: str, *, limit: int = 3000) -> str:
    output = output.strip()
    if not output:
        return "Manim failed or produced an empty render (no output captured)."
    marker = "LaTeX compilation error"
    marker_index = output.find(marker)
    if marker_index != -1:
        excerpt = output[marker_index : marker_index + 1200]
        return f"{excerpt}\n\n...\n{output[-limit:]}"
    summary = _summarize_traceback(output)
    return f"{summary}\n\n{output[-limit:]}" if summary else output[-limit:]


def _extract_keyframes(video_path: str, output: Path, ffmpeg_binary: str = "ffmpeg") -> list[str]:
    """Create a small deterministic frame set for visual verification."""
    ffmpeg = resolve_ffmpeg(ffmpeg_binary)
    if not ffmpeg:
        return []
    frames = output / "keyframes"
    frames.mkdir(exist_ok=True)
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-vf",
            "fps=1/4",
            "-frames:v",
            "8",
            str(frames / "frame-%02d.png"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        return []
    return [str(path) for path in sorted(frames.glob("*.png"))]


@dataclass
class RenderResult:
    success: bool
    video_path: str | None
    log_path: str
    metadata_path: str
    keyframe_paths: list[str]
    duration_seconds: float
    return_code: int
    error: str | None = None
    bounds_path: str | None = None


_BOUNDS_PATH_TOKEN = "__SCHOLARMOTION_BOUNDS_PATH__"

_BOUNDS_INSTRUMENTATION = """

import json as _sm_json
from manim import Scene as _SMScene

_sm_snapshots = []
# Thin strokes whose bounding box says little about what they actually cover.
_SM_STROKES = {
    "Line", "DashedLine", "Arrow", "DoubleArrow", "Vector", "Arc",
    "ParametricFunction", "FunctionGraph", "CurvedArrow",
}


def _sm_capture_snapshot(scene):
    bounds = []
    flattened = []

    def _sm_leaves(mob):
        # Container groups duplicate the bounds of their member objects. Record
        # their drawable leaves so the verifier can detect real text collisions
        # without reporting a diagram group overlapping its own arrows/labels.
        if type(mob).__name__ in ("VGroup", "Group", "Matrix", "MobjectMatrix", "DecimalMatrix", "IntegerMatrix") and getattr(mob, "submobjects", None):
            for child in mob.submobjects:
                yield from _sm_leaves(child)
        else:
            yield mob

    seen = set()
    for root in scene.mobjects:
        for mob in _sm_leaves(root):
            if id(mob) in seen:
                continue
            seen.add(id(mob))
            flattened.append(mob)

    for index, mob in enumerate(flattened):
        try:
            left = mob.get_left()[0]
            right = mob.get_right()[0]
            bottom = mob.get_bottom()[1]
            top = mob.get_top()[1]
        except Exception:
            continue
        if (right - left) < 1e-6 and (top - bottom) < 1e-6:
            continue
        is_text = type(mob).__name__ in ("Text", "MathTex", "Tex", "MarkupText", "SafeTitle", "STEMEquation")
        try:
            font_size = float(mob.font_size) if is_text and hasattr(mob, "font_size") else None
        except Exception:
            font_size = None
        stroke_points = None
        if type(mob).__name__ in _SM_STROKES:
            try:
                samples = [mob.point_from_proportion(i / 47) for i in range(48)]
                stroke_points = [[float(p[0]), float(p[1])] for p in samples]
            except Exception:
                stroke_points = None
        bounds.append(
            {
                "name": f"{type(mob).__name__}_{index}",
                "left": float(left),
                "right": float(right),
                "bottom": float(bottom),
                "top": float(top),
                "text_height": float(top - bottom) if is_text else None,
                "font_size": font_size,
                "points": stroke_points,
                "reserved_region": getattr(mob, "reserved_region", None),
                "label": str(getattr(mob, "text", getattr(mob, "tex_string", getattr(mob, "notation", ""))))[:160] if is_text else None,
            }
        )
    _sm_snapshots.append(bounds)


_sm_original_play = _SMScene.play
_sm_original_wait = _SMScene.wait
_sm_original_render = _SMScene.render


def _sm_patched_play(self, *args, **kwargs):
    result = _sm_original_play(self, *args, **kwargs)
    try:
        _sm_capture_snapshot(self)
    except Exception:
        pass
    return result


def _sm_patched_wait(self, *args, **kwargs):
    result = _sm_original_wait(self, *args, **kwargs)
    try:
        _sm_capture_snapshot(self)
    except Exception:
        pass
    return result


def _sm_patched_render(self, *args, **kwargs):
    result = _sm_original_render(self, *args, **kwargs)
    try:
        with open(__SCHOLARMOTION_BOUNDS_PATH__, "w") as _f:
            _sm_json.dump(_sm_snapshots, _f)
    except Exception:
        pass
    return result


_SMScene.play = _sm_patched_play
_SMScene.wait = _sm_patched_wait
_SMScene.render = _sm_patched_render
"""

# Pango falls back to an empty "tofu" box for glyphs the font lacks; Unicode
# superscripts/subscripts such as the transpose in QKᵀ or the k in dₖ are the
# usual culprits. Generated code can build those strings dynamically (f-strings,
# joins), which static rewriting cannot see, so normalize them at construction.
_GLYPH_GUARD = """

import manim as _sm_manim
import PIL.Image as _sm_pil_image

# Manim 0.22 builds the image-mobject canvas with fromarray(zeros 2-D float,
# mode="RGBA"), which newer Pillow rejects. Hand it an empty RGBA canvas.
_sm_fromarray = _sm_pil_image.fromarray


def _sm_safe_fromarray(obj, mode=None):
    try:
        return _sm_fromarray(obj) if mode is None else _sm_fromarray(obj, mode=mode)
    except (TypeError, ValueError):
        import numpy as _sm_np

        array = _sm_np.asarray(obj)
        if array.ndim == 2 and not array.any():
            return _sm_pil_image.new(mode or "RGBA", (array.shape[1], array.shape[0]))
        return _sm_fromarray(array.astype("uint8"))


_sm_pil_image.fromarray = _sm_safe_fromarray

_SM_GLYPHS = str.maketrans({
    "ᵀ": "^T", "⊤": "^T", "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4",
    "₅": "_5", "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9", "ₐ": "_a", "ₑ": "_e",
    "ᵢ": "_i", "ⱼ": "_j", "ₖ": "_k", "ₗ": "_l", "ₘ": "_m", "ₙ": "_n", "ₒ": "_o",
    "ₚ": "_p", "ₛ": "_s", "ₜ": "_t", "ₓ": "_x", "⁰": "^0", "¹": "^1", "²": "^2",
    "³": "^3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁺": "^+", "⁻": "^-", "ⁿ": "^n", "ⁱ": "^i",
})


# Pango's default font has no Indic, Arabic or CJK glyphs and draws every such
# character as a box, so narration-language labels need a font that covers the
# script. Candidates cover Windows first, then the usual Linux packages.
_SM_SCRIPT_FONTS = (
    (0x0900, 0x097F, ("Nirmala UI", "Noto Sans Devanagari", "Mangal", "Lohit Devanagari")),
    (0x0980, 0x09FF, ("Nirmala UI", "Noto Sans Bengali", "Vrinda", "Lohit Bengali")),
    (0x0B80, 0x0BFF, ("Nirmala UI", "Noto Sans Tamil", "Latha", "Lohit Tamil")),
    (0x0C00, 0x0C7F, ("Nirmala UI", "Noto Sans Telugu", "Gautami", "Lohit Telugu")),
    (0x0600, 0x06FF, ("Segoe UI", "Noto Sans Arabic", "Arial")),
    (0x3040, 0x30FF, ("Yu Gothic", "Noto Sans CJK JP", "Noto Sans JP", "Microsoft YaHei")),
    (0x4E00, 0x9FFF, ("Yu Gothic", "Noto Sans CJK JP", "Noto Sans JP", "Microsoft YaHei")),
)
_sm_installed_fonts = None


def _sm_script_font(text):
    global _sm_installed_fonts
    for low, high, candidates in _SM_SCRIPT_FONTS:
        if any(low <= ord(char) <= high for char in text):
            if _sm_installed_fonts is None:
                try:
                    import manimpango as _sm_pango

                    _sm_installed_fonts = set(_sm_pango.list_fonts())
                except Exception:
                    _sm_installed_fonts = set()
            return next((name for name in candidates if name in _sm_installed_fonts), None)
    return None


def _sm_guard_text(cls):
    original = cls.__init__

    def guarded(self, *args, **kwargs):
        args = tuple(a.translate(_SM_GLYPHS) if isinstance(a, str) else a for a in args)
        if isinstance(kwargs.get("text"), str):
            kwargs["text"] = kwargs["text"].translate(_SM_GLYPHS)
        if not kwargs.get("font"):
            text = "".join(a for a in args if isinstance(a, str)) + str(kwargs.get("text") or "")
            font = _sm_script_font(text)
            if font:
                kwargs["font"] = font
        original(self, *args, **kwargs)

    cls.__init__ = guarded


for _sm_name in ("Text", "MarkupText", "Paragraph"):
    if hasattr(_sm_manim, _sm_name):
        _sm_guard_text(getattr(_sm_manim, _sm_name))
"""


class SandboxRenderer:
    """Static gate plus constrained process runner.

    The process runner validates AST imports/calls, runs in an empty temporary
    directory, drops most inherited environment, starts a new process group,
    and applies CPU, address-space, process-count, and file-descriptor limits.
    It is a local development boundary, not a complete VM/container boundary:
    multi-user production must execute it in an OS/container sandbox with
    network isolation enforced outside this process.
    """

    def __init__(
        self,
        manim_binary: str = "manim",
        timeout_seconds: int = 180,
        mock_missing: bool = True,
        memory_limit_mb: int = 2048,
        cpu_seconds: int = 180,
        process_limit: int = 0,
        ffmpeg_binary: str = "ffmpeg",
        isolation_mode: str = "auto",
    ):
        self.manim_binary = manim_binary
        self.timeout_seconds = timeout_seconds
        self.mock_missing = mock_missing
        self.memory_limit_mb = memory_limit_mb
        self.cpu_seconds = cpu_seconds
        self.process_limit = process_limit
        self.ffmpeg_binary = ffmpeg_binary
        if isolation_mode not in {"auto", "required", "disabled"}:
            raise ValueError("isolation_mode must be auto, required, or disabled")
        self.isolation_mode = isolation_mode

    def render(
        self,
        code: str,
        scene_class: str,
        output_dir: str | Path,
        *,
        assets_dir: str | Path | None = None,
    ) -> RenderResult:
        started = time.monotonic()
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        log_path = output / "render.log"
        metadata_path = output / "metadata.json"
        validation = validate_generated_code(code)
        if not validation.accepted or scene_class not in validation.scene_classes:
            error = "; ".join(validation.errors or [f"missing scene class {scene_class}"])
            log_path.write_text(error, encoding="utf-8")
            result = RenderResult(
                False,
                None,
                str(log_path),
                str(metadata_path),
                [],
                time.monotonic() - started,
                2,
                error,
            )
            metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            return result
        executable = shutil.which(self.manim_binary)
        ffmpeg_executable = resolve_ffmpeg(self.ffmpeg_binary)
        bubblewrap = shutil.which("bwrap") if os.name == "posix" else None
        if self.isolation_mode == "required" and not bubblewrap:
            error = "OS renderer isolation is required, but bubblewrap (bwrap) is unavailable"
            log_path.write_text(error, encoding="utf-8")
            result = RenderResult(
                False,
                None,
                str(log_path),
                str(metadata_path),
                [],
                time.monotonic() - started,
                126,
                error,
            )
            metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            return result
        if not executable and self.mock_missing:
            video_path = output / "draft.mp4"
            video_path.write_bytes(b"SCHOLARMOTION_DETERMINISTIC_RENDER\n" + scene_class.encode())
            log_path.write_text(
                "Manim unavailable; deterministic render artifact created.\n", encoding="utf-8"
            )
            result = RenderResult(
                True,
                str(video_path),
                str(log_path),
                str(metadata_path),
                [],
                time.monotonic() - started,
                0,
            )
            metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            return result
        bounds_path = output / "bounds.json"
        with tempfile.TemporaryDirectory(prefix="scholarmotion-render-") as temp:
            work = Path(temp)
            code_path = work / "scene.py"
            instrumented = (
                code
                + _GLYPH_GUARD
                + _BOUNDS_INSTRUMENTATION.replace(_BOUNDS_PATH_TOKEN, repr(str(bounds_path)))
            )
            code_path.write_text(instrumented, encoding="utf-8")
            if assets_dir:
                source_assets = Path(assets_dir).resolve()
                if source_assets.exists():
                    shutil.copytree(source_assets, work / "assets", dirs_exist_ok=True)
            env = {
                "PATH": os.pathsep.join(
                    filter(
                        None,
                        [
                            str(Path(executable).parent) if executable else "",
                            str(Path(ffmpeg_executable).parent) if ffmpeg_executable else "",
                            os.environ.get("PATH", ""),
                        ],
                    )
                ),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                # User-level Conda runtimes may supply Cairo/Pango libraries
                # required by a project-local Manim install.
                "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_PROXY": "*",
                # Manim imports NumPy/OpenBLAS.  A renderer does not benefit
                # from a large numerical worker pool, and its thread stacks
                # can consume the entire address-space budget before the
                # scene starts.  Pinning native pools also prevents generated
                # code from exhausting the process limit through BLAS.
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
                "HOME": str(work),
                "TMPDIR": str(work / "tmp"),
            }
            if os.name == "nt":
                # Windows resolves Path.home() from USERPROFILE, and native
                # libraries need the OS directory even in the stripped child.
                env.update(
                    USERPROFILE=str(work),
                    APPDATA=str(work / "AppData" / "Roaming"),
                    LOCALAPPDATA=str(work / "AppData" / "Local"),
                    TEMP=str(work / "tmp"),
                    TMP=str(work / "tmp"),
                    PYTHONUTF8="1",
                )
                for name in ("SYSTEMROOT", "WINDIR"):
                    if os.environ.get(name):
                        env[name] = os.environ[name]
            (work / "tmp").mkdir()
            command = [
                executable or self.manim_binary,
                "-ql",
                "--disable_caching",
                "--media_dir",
                str(output),
                str(code_path),
                scene_class,
            ]
            use_bubblewrap = bubblewrap and self.isolation_mode != "disabled"
            if use_bubblewrap:
                # The host filesystem is visible read-only. Generated code can
                # write only into its scratch directory and this scene's output
                # directory; all namespaces, especially network, are isolated.
                command = [
                    bubblewrap,
                    "--die-with-parent",
                    "--unshare-all",
                    "--ro-bind",
                    "/",
                    "/",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--bind",
                    str(work),
                    str(work),
                    "--bind",
                    str(output),
                    str(output),
                    "--chdir",
                    str(work),
                    "--",
                    *command,
                ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    shell=False,
                    check=False,
                    preexec_fn=_resource_limits(
                        self.memory_limit_mb, self.cpu_seconds, self.process_limit
                    )
                    if os.name == "posix"
                    else None,
                )
                log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
                candidates = sorted(
                    output.rglob(f"{scene_class}.mp4"), key=lambda p: p.stat().st_mtime
                )
                video_path = str(candidates[-1]) if candidates else None
                success = (
                    completed.returncode == 0
                    and bool(video_path)
                    and Path(video_path).stat().st_size > 0
                )
                error = None if success else _tail_error(completed.stdout + "\n" + completed.stderr)
                keyframes = (
                    _extract_keyframes(video_path, output, self.ffmpeg_binary)
                    if success and video_path
                    else []
                )
                result = RenderResult(
                    success,
                    video_path,
                    str(log_path),
                    str(metadata_path),
                    keyframes,
                    time.monotonic() - started,
                    completed.returncode,
                    error,
                    str(bounds_path) if success and bounds_path.exists() else None,
                )
            except subprocess.TimeoutExpired as exc:

                def _decode(value: str | bytes | None) -> str:
                    if value is None:
                        return ""
                    return (
                        value.decode("utf-8", errors="replace")
                        if isinstance(value, bytes)
                        else value
                    )

                log_path.write_text(
                    _decode(exc.stdout) + "\n" + _decode(exc.stderr), encoding="utf-8"
                )
                result = RenderResult(
                    False,
                    None,
                    str(log_path),
                    str(metadata_path),
                    [],
                    time.monotonic() - started,
                    124,
                    "render timeout",
                )
        metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        return result
