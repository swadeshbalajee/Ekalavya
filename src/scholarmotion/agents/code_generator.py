"""Manim code generation for one scene, with LaTeX/Unicode fallbacks and repair prompts."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import re
import shutil
from functools import lru_cache
from pathlib import Path

from scholarmotion.manim_runtime.templates import choose_template, with_cues
from scholarmotion.media.math_notation import (
    ascii_math,
    contains_native_prose,
    needs_math_typesetting,
)
from scholarmotion.schemas import GeneratedSceneCode, SceneSpec

logger = logging.getLogger(__name__)


CODE_RETRY_DELAY_SECONDS = 5.0


class CodeGenerationError(RuntimeError):
    """The code model could not produce a scene after bounded retries."""


def scene_class_name(scene_id: str) -> str:
    """Return a stable, valid Python class name for an external scene identifier.

    Scene IDs are API input and may contain separators such as ``startup-smoke``.
    They remain unchanged in the SceneSpec/database; only the generated Python
    symbol is normalised.
    """
    identifier = re.sub(r"[^0-9A-Za-z_]", "_", scene_id)
    return f"Scene{identifier or 'Generated'}"


@lru_cache(maxsize=1)
def latex_available() -> bool:
    """Whether Manim can compile Tex/MathTex on this machine.

    Manim shells out to `latex` for every Tex-based mobject. Without a TeX
    distribution installed those renders raise FileNotFoundError, which the
    repair loop cannot fix by rewriting code it keeps regenerating the same way.
    """
    return shutil.which("latex") is not None


NO_LATEX_RULES = """
LATEX IS NOT INSTALLED ON THIS MACHINE. MathTex, Tex, and EquationPanel cannot render and will
crash the scene. This overrides the equation rules above: express every formula, Greek letter,
and symbol with Text(...) using explicit ASCII powers and indices, for example Text("QK^T"),
Text("E proportional to 1/r^3"), Text("tau = p x E"), Text("theta"), Text("epsilon_0").
Do not rely on font support for Unicode superscripts or subscripts. Do not import or call
MathTex, Tex, STEMEquation, or EquationPanel anywhere."""

RULES = """Use only Manim, math, numpy, sympy, and scholarmotion.manim_runtime.components.
Use Manim Matrix or MobjectMatrix for score grids and masks rather than manually positioning
individual entries. Matrix row indices increase downward; do not invert the rows on screen.
Keep one central diagram at a time, with equations in a separate band. Remove temporary copies
after transformations so duplicate digits and orphaned symbols do not remain visible.
Treat every equation in SCENE SPEC `equations` as immutable canonical STEM notation: copy its
symbols, signs, subscripts, superscripts, and constants exactly. Translate nearby labels only; never
translate, paraphrase, or reconstruct an equation from the localized narration. Render required
equations exactly as supplied, and do not invent a different equivalent form.
Render each required equation from its full canonical string in a single MathTex or STEMEquation
call. Do not split its source across fragments; use a copy of the whole equation for transformations.
Never access network, process, shell, environment, or paths outside the supplied assets directory.
Use SafeTitle and reserve SubtitleSafeRegion. Use avoid_overlap for simultaneously visible
labels after positioning them. Treat layout faults as render failures: do not put text on top
of unrelated text, linework, or diagram parts, and reserve at least 0.25 scene units between
unrelated labels. Keep labels in a separate VGroup from the diagram so each group can be
positioned without scaling the other. Never put `self.mobjects` in a VGroup; use Group for
heterogeneous scene objects, since scene roots may include non-VMobject instances.
Use a fixed 14.22 by 8 scene grid: title centered at y=3.25; main illustration within
x=[-5.5,5.5], y=[-1.8,2.45]; equation/short labels in a separate band at y=[-2.55,-1.95];
subtitles need the band below y=-2.8. The coordinates are layout limits, not suggestions. Keep
the title in the top band and all other labels below y=2.3. Place labels beside their own lines
and outside arrow paths; do not put an area label where field arrows cross it. Labels may sit
inside a filled shape when that containment is intentional. Keep circles and expanding diagrams
small enough to remain inside the illustration band at their largest animation state. Never scale
a title or equation up to fill a width; only scale it down when its measured width exceeds its band.
Use SafeTitle rather than Manim Title, which requires a TeX installation.
Use only the Manim direction constants UP, DOWN, LEFT, RIGHT, UL, UR, DL, and DR; do not invent
names such as UP_LEFT or DOWN_RIGHT. Keep visual layouts sparse: use one main diagram plus at most
three labels per keyframe, keep all content above y=-2.5, and fade out an old diagram before
showing a replacement. Never place a large diagram, area model, equation, and worked example on
top of one another; show them in separate beats or in distinct left/right regions. For geometry,
keep labels beside their own lines, and move area models away from the original shape before
showing equations. Arrange related objects as a group before positioning them. When animating a
Python list of Mobjects (for example `arrows = [...]`), never pass the list to FadeIn/FadeOut/
Write/Create; use `VGroup(*arrows)` or animate each item separately. A Python list is not a
Manim Mobject. Treat a short learner request as a request for a complete, intuitive lesson: establish
the clearest domain-appropriate physical setup, observable mechanism, or data example and animate
the causal relationship being explained. Do not invent or prescribe an analogy unless the learner
explicitly requested one. Before writing code, follow the ordered visual beats: establish that setup,
reveal the formal representation, work a small example, animate the
cause and effect, and end with a check or synthesis when the beat plan allows. Make each beat teach
a new step; do not substitute decorative shapes or generic axes.
HARD AUDIO/TEXT CONTRACT: `narration` is the spoken audio transcript and subtitle source only.
NEVER copy any narration sentence or phrase into Text, MarkupText, SafeTitle, TimelineLabel, or
another Manim text object. Do not create subtitles, captions, sentence cards, or spoken-text
overlays in Manim. On-screen text may contain only a short title, concise labels of at most five
words, symbols, equations, numeric values in a worked example, and short source/page markers. The
narration can guide timing and visual order, but is not a visual asset.
The component helpers have these exact call signatures: create the safe region with
`subtitle_region = SubtitleSafeRegion()` (never pass `self`), and call
`keep_inside_frame(mobject)` (never pass the Scene instance). Add the region to the scene when
needed with `self.add(subtitle_region)`.
Define exactly the requested Scene subclass. Keep each scene independent.
Use `Create` only for individual VMobjects or VGroups. A `Group` may contain base Mobjects such
as text and safe regions; introduce it with `FadeIn` rather than `Create`.
Never pass LaTeX source (backslash commands like \\Phi, \\frac, or a string wrapped in $...$) to
Text(), SafeTitle(), or TimelineLabel() — they render it as literal characters, not a symbol.
Any equation, Greek letter, or mathematical symbol MUST go through MathTex(...) or
EquationPanel(equation=..., title=...) / EquationPanel(equations=[...], title=...) instead.
Conversely, MathTex/Tex/EquationPanel are ONLY for actual mathematical notation — never put a
plain English caption, sentence, or label there (use Text/SafeTitle/EquationPanel's own `title=`
kwarg for that instead). If a short word label truly must appear inside a MathTex string via
\\text{...}, it must never contain a literal &, %, or # character (LaTeX treats these as special
control characters — alignment tab, comment, parameter — and compilation will fail); escape them
as \\&, \\%, \\# if unavoidable.
Mobject has no `.bounding_box` attribute and no `.get_bounding_box()` method in this Manim
version; to compute extents use get_left()/get_right()/get_top()/get_bottom() (each returns an
x/y/z point) or the `.width`/`.height` properties.
Mobject has no `.scale_about_point(factor, point)` method; to scale around a specific point use
`.scale(factor, about_point=point)` instead (works both directly and inside `.animate`).
Use APIs available in Manim Community 0.20: there is no `RightTriangle` class (use Polygon with
vertices); call `mobject.align_to(other, direction)` with positional arguments; center with
`move_to(ORIGIN)`; and use `always_redraw`, not `always_redirected`. Import project helpers from
`scholarmotion.manim_runtime.components`.
For Indicate animations, use the keyword `scale_factor`, never `scale`. Pass `lag_ratio` to
Scene.play when staggering its animations; FadeIn itself does not accept `lag` or `lag_ratio`.
Arc accepts `angle`, not
`quarter`; omit `quarter` and set `start_angle` / `angle` instead. The rectangle helper is named
`SurroundingRectangle`, not `SurroundRectangle`. Manim Triangle() does not accept a `sides` keyword;
use Polygon(vertices...) to create a custom triangle. There is no `DASHED` style constant: wrap a
shape in DashedVMobject(shape). Mobjects have no `get_boundary()` method; use explicit start/end
coordinates or get_center()."""


def normalize_manim_direction_constants(
    code: str, *, display_title: str | None = None, latex_enabled: bool = True
) -> str:
    for unsupported, supported in {
        "UP_LEFT": "UL",
        "UP_RIGHT": "UR",
        "DOWN_LEFT": "DL",
        "DOWN_RIGHT": "DR",
        # These plausible-looking color names are not part of Manim CE's
        # palette. Map common model hallucinations to the closest real colors.
        "GRAY_DARKER": "GRAY_D",
        "BLUE_GRAY": "BLUE_E",
        # Manim Community does not export a CYAN constant. Models use it
        # frequently for magnetic-field diagrams, so normalize it to the
        # closest supported palette entry before rendering.
        "CYAN": "TEAL_A",
    }.items():
        code = re.sub(rf"\b{unsupported}\b", supported, code)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    _group_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Group"
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    class NormalizeIndicateScale(ast.NodeTransformer):
        changed = False
        needs_future_annotations = False
        group_names = _group_names

        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "Text"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and needs_math_typesetting(node.args[0].value)
            ):
                if latex_enabled and not contains_native_prose(node.args[0].value):
                    node.func.id = "STEMEquation"
                    node.keywords = [
                        kw
                        for kw in node.keywords
                        if kw.arg not in {"font", "disable_ligatures", "t2c", "t2f", "line_spacing"}
                    ]
                else:
                    node.args[0].value = ascii_math(node.args[0].value)
                self.changed = True
            # A model occasionally copies the opening instruction into the
            # title (for example, "Create a two-minute beginner visual
            # lesson").  It is technically renderable but reads like a prompt,
            # not a lesson.  Replace only unmistakable generation framing;
            # legitimate short topical headings remain untouched.
            if (
                display_title
                and isinstance(node.func, ast.Name)
                and node.func.id in {"SafeTitle", "Text"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and re.match(
                    r"^(?:create|make|generate|build)\s+"
                    r"(?:(?:a|an|the)\s+)?"
                    r"(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
                    r"(?:[-\s]minute))?\s*"
                    r"(?:(?:beginner|visual|natural|concise|friendly)\s+)*"
                    r"(?:video|visual\s+lesson|lesson|explanation)\b",
                    node.args[0].value,
                    flags=re.IGNORECASE,
                )
            ):
                node.args[0] = ast.Constant(value=display_title)
                self.changed = True
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "Create"
                and node.args
                and (
                    (isinstance(node.args[0], ast.Name) and node.args[0].id in self.group_names)
                    or (
                        isinstance(node.args[0], ast.Call)
                        and isinstance(node.args[0].func, ast.Name)
                        and node.args[0].func.id == "Group"
                    )
                )
            ):
                node.func.id = "FadeIn"
                self.changed = True
            if isinstance(node.func, ast.Name) and node.func.id == "VGroup":
                contains_scene_mobjects = any(
                    isinstance(argument, ast.Starred)
                    and isinstance(argument.value, ast.Attribute)
                    and isinstance(argument.value.value, ast.Name)
                    and argument.value.value.id == "self"
                    and argument.value.attr == "mobjects"
                    for argument in node.args
                )
                if contains_scene_mobjects:
                    # Scene.mobjects can include base Mobject instances such
                    # as the subtitle safe region; VGroup accepts VMobjects
                    # only, while Group supports all Mobject subclasses.
                    node.func.id = "Group"
                    self.changed = True
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
                and node.func.value.attr == "config"
                and node.args
            ):
                self.changed = True
                return node.args[-1]
            if not latex_enabled and isinstance(node.func, ast.Name):
                if node.func.id in {"MathTex", "Tex"}:
                    node.func.id = "Text"
                    node.args = [
                        ast.Constant(value=_latex_to_unicode(item.value))
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                        else item
                        for item in node.args
                    ]
                    self.changed = True
                elif node.func.id == "EquationPanel":
                    equation = next(
                        (item.value for item in node.keywords if item.arg == "equation"),
                        node.args[0] if node.args else ast.Constant(value="Equation"),
                    )
                    if isinstance(equation, ast.Constant) and isinstance(equation.value, str):
                        equation.value = _latex_to_unicode(equation.value)
                    node.func.id = "Text"
                    node.args = [equation]
                    node.keywords = [
                        item for item in node.keywords if item.arg in {"font_size", "color"}
                    ]
                    self.changed = True
            if isinstance(node.func, ast.Name) and node.func.id in {"ImageMobject", "SVGMobject"}:
                asset_name = (
                    node.args[0].value
                    if node.args and isinstance(node.args[0], ast.Constant)
                    else "diagram"
                )
                node.func.id = "Text"
                node.args = [
                    ast.Constant(value=Path(str(asset_name)).stem.replace("_", " ").title())
                ]
                node.keywords = []
                self.changed = True
            # Models often put ``lag`` on a FadeIn or use it on Scene.play.
            # Manim's FadeIn has no lag keyword; Scene.play calls it lag_ratio.
            if isinstance(node.func, ast.Attribute) and node.func.attr == "play":
                for keyword in node.keywords:
                    if keyword.arg == "lag":
                        keyword.arg = "lag_ratio"
                        self.changed = True
            if isinstance(node.func, ast.Name) and node.func.id in {"FadeIn", "FadeOut"}:
                unsupported = [item for item in node.keywords if item.arg in {"lag", "lag_ratio"}]
                if unsupported:
                    node.keywords = [
                        item for item in node.keywords if item.arg not in {"lag", "lag_ratio"}
                    ]
                    self.changed = True
            if isinstance(node.func, ast.Name):
                # Title is implemented with TeX in Manim CE. This environment
                # deliberately supports Unicode Text without a TeX install;
                # use the project wrapper so generated headings render here.
                if node.func.id == "Title":
                    node.func.id = "SafeTitle"
                    self.changed = True
                if node.func.id == "RightTriangle":
                    node.func.id = "Triangle"
                    self.changed = True
                if node.func.id == "always_redirected":
                    node.func.id = "always_redraw"
                    self.changed = True
                if node.func.id == "CurvedArrow":
                    # Manim CE names these constructor arguments
                    # ``start_point`` and ``end_point``.  Models frequently
                    # use the shorter Arrow-style keywords, which fail only
                    # at render time.
                    for keyword in node.keywords:
                        if keyword.arg == "start":
                            keyword.arg = "start_point"
                            self.changed = True
                        elif keyword.arg == "end":
                            keyword.arg = "end_point"
                            self.changed = True
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "get_boundary":
                    node.func.attr = "get_center"
                    self.changed = True
                if (
                    node.func.attr == "set_stroke"
                    and isinstance(node.func.value, ast.Call)
                    and any(
                        keyword.arg == "style"
                        and isinstance(keyword.value, ast.Name)
                        and keyword.value.id == "DASHED"
                        for keyword in node.keywords
                    )
                ):
                    shape = node.func.value
                    other_keywords = [
                        keyword for keyword in node.keywords if keyword.arg != "style"
                    ]
                    return ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="DashedVMobject", ctx=ast.Load()),
                            args=[shape],
                            keywords=other_keywords,
                        ),
                        node,
                    )
                if node.func.attr in {"set_x", "set_y"} and node.args:
                    vector_getters = {
                        "get_left",
                        "get_right",
                        "get_center",
                        "get_top",
                        "get_bottom",
                        "get_corner",
                        "get_start",
                        "get_end",
                    }
                    has_vector_coordinate = any(
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in vector_getters
                        for child in ast.walk(node.args[0])
                    )
                    if has_vector_coordinate:
                        node.args[0] = ast.Subscript(
                            value=node.args[0],
                            slice=ast.Constant(value=0 if node.func.attr == "set_x" else 1),
                            ctx=ast.Load(),
                        )
                        self.changed = True
                if node.func.attr == "to_center":
                    node.func.attr = "move_to"
                    node.args = [ast.Name(id="ORIGIN", ctx=ast.Load())]
                    node.keywords = []
                    self.changed = True
                if node.func.attr == "align_to":
                    aligned_edge = next(
                        (item for item in node.keywords if item.arg == "aligned_edge"), None
                    )
                    if aligned_edge is not None:
                        node.keywords.remove(aligned_edge)
                        node.args.append(aligned_edge.value)
                        self.changed = True
            if isinstance(node.func, ast.Name) and node.func.id == "SafeTitle":
                for keyword in node.keywords:
                    if keyword.arg == "title":
                        keyword.arg = "subtitle"
                        self.changed = True
                if (
                    display_title
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and len(node.args[0].value) > 60
                ):
                    node.args[0] = ast.Constant(value=display_title)
                    self.changed = True
            unsupported_runtime_kwargs = [
                item for item in node.keywords if item.arg == "subtitle_safe_region"
            ]
            if unsupported_runtime_kwargs:
                node.keywords = [
                    item for item in node.keywords if item.arg != "subtitle_safe_region"
                ]
                self.changed = True
            if isinstance(node.func, ast.Name):
                if node.func.id == "Polygon":
                    for index, vertex in enumerate(node.args):
                        if (
                            isinstance(vertex, ast.Call)
                            and isinstance(vertex.func, ast.Name)
                            and vertex.func.id == "Point"
                            and len(vertex.args) in {2, 3}
                        ):
                            coordinates = [
                                *vertex.args[:2],
                                *(vertex.args[2:3] or [ast.Constant(0)]),
                            ]
                            node.args[index] = ast.Call(
                                func=ast.Attribute(
                                    value=ast.Name(id="np", ctx=ast.Load()),
                                    attr="array",
                                    ctx=ast.Load(),
                                ),
                                args=[ast.List(elts=coordinates, ctx=ast.Load())],
                                keywords=[],
                            )
                            self.changed = True
                if node.func.id == "SurroundRectangle":
                    node.func.id = "SurroundingRectangle"
                    self.changed = True
                if node.func.id in {"Triangle", "Star"}:
                    unsupported = [item for item in node.keywords if item.arg == "sides"]
                    if unsupported:
                        triangle_sides = next(
                            (item.value for item in unsupported if node.func.id == "Triangle"),
                            None,
                        )
                        if (
                            isinstance(triangle_sides, ast.Call)
                            and isinstance(triangle_sides.func, ast.Attribute)
                            and triangle_sides.func.attr == "array"
                            and triangle_sides.args
                        ):
                            triangle_sides = triangle_sides.args[0]
                        node.keywords = [item for item in node.keywords if item.arg != "sides"]
                        if node.func.id == "Triangle" and isinstance(triangle_sides, ast.List):
                            sides = [
                                item.value
                                for item in triangle_sides.elts
                                if isinstance(item, ast.Constant)
                                and isinstance(item.value, (int, float))
                            ]
                            if sorted(sides) == [3, 4, 5]:
                                node.func.id = "Polygon"
                                node.args = [
                                    ast.BinOp(
                                        left=ast.Name(id="LEFT", ctx=ast.Load()),
                                        op=ast.Mult(),
                                        right=ast.Constant(0),
                                    ),
                                    ast.BinOp(
                                        left=ast.Name(id="RIGHT", ctx=ast.Load()),
                                        op=ast.Mult(),
                                        right=ast.Constant(4),
                                    ),
                                    ast.BinOp(
                                        left=ast.Name(id="UP", ctx=ast.Load()),
                                        op=ast.Mult(),
                                        right=ast.Constant(3),
                                    ),
                                ]
                        self.changed = True
                if node.func.id == "Indicate":
                    for keyword in node.keywords:
                        if keyword.arg == "scale":
                            keyword.arg = "scale_factor"
                            self.changed = True
                elif node.func.id == "Arc":
                    unsupported = [item for item in node.keywords if item.arg == "quarter"]
                    if unsupported:
                        node.keywords = [item for item in node.keywords if item.arg != "quarter"]
                        self.changed = True
            return node

        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
            if node.module == "scholarmotion.manim_runtime.primitives":
                node.module = "scholarmotion.manim_runtime.components"
                self.changed = True
            if node.module and node.module.startswith("manim."):
                node.module = "manim"
                self.changed = True
            if node.module == "typing":
                self.changed = True
                self.needs_future_annotations = True
                return None
            if node.module == "scholarmotion.manim_runtime.components":
                allowed = {
                    "fit_to_region",
                    "keep_inside_frame",
                    "avoid_overlap",
                    "SafeTitle",
                    "EquationPanel",
                    "LabeledAxes",
                    "StepEquationTransform",
                    "VectorArrow",
                    "ComparisonTable",
                    "PaperFigurePanel",
                    "mark_reserved",
                    "SubtitleSafeRegion",
                    "HighlightBox",
                    "TimelineLabel",
                    "STEMEquation",
                    "arrange_in_band",
                    "place",
                    "CueClock",
                    "REGIONS",
                    "FONT",
                    "paper_figure",
                    "figure_region",
                    "figure_point",
                }
                if any(alias.name == "*" for alias in node.names):
                    return node
                filtered = [alias for alias in node.names if alias.name in allowed]
                if not filtered:
                    self.changed = True
                    return None
                if len(filtered) != len(node.names):
                    self.changed = True
                    node.names = filtered
            if node.module == "manim":
                for alias in node.names:
                    if alias.name == "RightTriangle":
                        alias.name = "Triangle"
                        self.changed = True
            return node

        def visit_List(self, node: ast.List) -> ast.AST:
            self.generic_visit(node)
            if len(node.elts) == 3 and all(
                isinstance(item, ast.Constant) and isinstance(item.value, (int, float))
                for item in node.elts
            ):
                self.changed = True
                return ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="np", ctx=ast.Load()),
                        attr="array",
                        ctx=ast.Load(),
                    ),
                    args=[node],
                    keywords=[],
                )
            return node

    normalizer = NormalizeIndicateScale()
    tree = normalizer.visit(tree)
    if not latex_enabled:
        assigned_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Param))
        }
        bare_resistance_used = any(
            isinstance(node, ast.Name) and node.id == "R" and isinstance(node.ctx, ast.Load)
            for node in ast.walk(tree)
        )
        if "R" not in assigned_names and bare_resistance_used:

            class NormalizeBareResistance(ast.NodeTransformer):
                def visit_Name(self, node: ast.Name):
                    if node.id == "R" and isinstance(node.ctx, ast.Load):
                        node.id = "RIGHT"
                    return node

            tree = NormalizeBareResistance().visit(tree)
            normalizer.changed = True
    if normalizer.changed:
        code = ast.unparse(ast.fix_missing_locations(tree))
        if "np.array(" in code and "import numpy as np" not in code:
            code = "import numpy as np\n" + code
    if "mcomp." in code and "import scholarmotion.manim_runtime.components as mcomp" not in code:
        code = "import scholarmotion.manim_runtime.components as mcomp\n" + code
    component_names = (
        "SafeTitle",
        "SubtitleSafeRegion",
        "keep_inside_frame",
        "avoid_overlap",
        "EquationPanel",
        "TimelineLabel",
        "STEMEquation",
        "arrange_in_band",
        "place",
        "CueClock",
        "REGIONS",
        "FONT",
        "paper_figure",
        "figure_region",
        "figure_point",
    )
    required_components = [name for name in component_names if re.search(rf"\b{name}\b", code)]
    if required_components:
        code = (
            "from scholarmotion.manim_runtime.components import "
            + ", ".join(required_components)
            + "\n"
            + code
        )
    if normalizer.needs_future_annotations and "from __future__ import annotations" not in code:
        code = "from __future__ import annotations\n" + code
    if "from manim import *" not in code:
        code = "from manim import *\n" + code
    if re.search(r"\bnp\.", code) and "import numpy as np" not in code:
        code = "import numpy as np\n" + code
    return code


def _latex_to_unicode(value: str) -> str:
    value = re.sub(r"\\text\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\(?:mathrm|mathbf)\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\left", "").replace(r"\right", "").replace(r"\,", " ")
    value = re.sub(
        r"\\frac\{((?:[^{}]|\{[^{}]*\})+)\}\{((?:[^{}]|\{[^{}]*\})+)\}",
        r"\1 / \2",
        value,
    )
    for command, symbol in {
        r"\phi": "φ",
        r"\Phi": "Φ",
        r"\Omega": "Ω",
        r"\theta": "θ",
        r"\mu": "μ",
        r"\epsilon": "ε",
        r"\sqrt": "√",
        r"\times": "×",
        r"\cdot": "·",
        r"\pi": "π",
        r"\rightarrow": "→",
        r"\partial": "∂",
        r"\nabla": "∇",
    }.items():
        value = value.replace(command, symbol)
    value = value.replace("^T", "ᵀ")
    for base, subscript in zip("0123456789", "₀₁₂₃₄₅₆₇₈₉"):
        value = value.replace("_" + base, subscript)
    value = value.replace("_k", "ₖ")
    for base, superscript in zip("234", "²³⁴"):
        value = value.replace("^" + base, superscript)
    return value.replace("{", "").replace("}", "").replace("$", "").replace("\\", "")


async def generate_scene_code(
    provider,
    spec: SceneSpec,
    corrections: list[str] | None = None,
    repair_feedback: list[str] | None = None,
    skill_context: str = "",
    narration: str = "",
    fallback_only: bool = False,
    sentence_durations: list[float] | None = None,
    previous_code: str = "",
    edit_request: str = "",
) -> GeneratedSceneCode:
    scene_class = scene_class_name(spec.scene_id)
    context = "\n".join(corrections or [])
    repairs = "\n".join(repair_feedback or [])
    rules = RULES if latex_available() else RULES + NO_LATEX_RULES
    fallback_spec = spec.model_copy(update={"narration": narration}) if narration else spec
    display_title = _short_scene_title(spec.title)
    scene_payload = spec.model_dump_json(indent=2, exclude={"narration"})
    spoken = narration or spec.narration
    cue_texts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", spoken) if part.strip()]
    durations = [max(0.2, value) for value in sentence_durations or []]
    if len(durations) != len(cue_texts):
        cue_texts = [spoken] if not durations else cue_texts[: len(durations)]
        durations = durations or [max(1.0, spec.duration_target_seconds)]
        cue_texts += [""] * (len(durations) - len(cue_texts))
    cue_plan = "\n".join(
        f'  cue {index}: {seconds:.2f}s  "{text}"'
        for index, (seconds, text) in enumerate(zip(durations, cue_texts, strict=True), 1)
    )
    template = choose_template(
        " ".join(
            [spec.title, spec.learning_objective, spoken]
            + [beat.visual for beat in spec.visual_beats]
        ),
        has_data_plot=bool(spec.data_plots),
        has_paper_figure=bool(spec.paper_figures),
    )
    figure_guidance = (
        "== PAPER FIGURES (original images; the attached pictures, in this order) ==\n"
        + "\n".join(
            f"{figure.label}: paper_figure({figure.filename!r}) is {figure.width}x{figure.height} "
            f"px. Caption: {figure.caption}"
            + (f" Point at, in order: {figure.focus}" if figure.focus else "")
            for figure in spec.paper_figures
        )
        + "\nShow each with paper_figure(filename, region); never redraw it. Point at parts with "
        "figure_region(image, x0, y0, x1, y1) boxes and figure_point(image, fx, fy) arrows, "
        "using fractions of the image measured from its top-left corner. The attached copy has "
        "a pink grid with lines every 0.1 (labels along the top for x, the left for y; the grid "
        "is not in the video): read each part's x0, y0, x1, y1 off that grid so every box "
        "surrounds exactly the part the voice names, not a legend or neighbouring panel. Move the "
        "highlight as the narration moves; keep the figure large (main region) while it is "
        "being explained, and shrink it to the left region only to show an equation beside "
        "it. Put the label badge (e.g. 'Figure 1') next_to the image.\n\n"
        if spec.paper_figures
        else ""
    )
    plot_guidance = (
        "== DATA PLOTS (values copied from the paper) ==\n"
        + "\n".join(
            f"{plot.plot_id} {plot.kind} chart '{plot.title}' from {plot.source or 'the paper'}: "
            f"x={plot.x_label!r}, y={plot.y_label!r}, categories={plot.categories}, "
            + "; ".join(f"{series.name}={series.values}" for series in plot.series)
            for plot in spec.data_plots
        )
        + "\nDraw each with exactly these numbers (BarChart for bar; Axes.plot_line_graph for "
        "line, one colour per series with a small legend placed next_to the axes). Show the "
        "source badge. Never rescale, round differently or add values. Label categories with "
        "Text at FONT['note'] placed next_to each bar or tick.\n\n"
        if spec.data_plots
        else ""
    )
    math_guidance = (
        "Use MathTex for equations. For canonical Unicode notation use STEMEquation; it converts "
        "transpose/subscripts to real TeX glyphs. Never put superscripts/subscripts in Text."
        if latex_available()
        else "TeX is unavailable. Use visible ASCII ^T and _k notation in Text."
    )
    # Text identical for every scene of a build comes first, so the provider
    # can mark it as a prompt-cache prefix: the shared rules and skills, then
    # the chosen template. Only the scene-specific remainder is new input.
    shared_rules = (
        "You write one Manim Community Edition scene (`from manim import *`; never manimlib) "
        "for a narrated STEM lesson. Speech is supplied as audio and subtitles outside Manim. "
        "Do not create captions or spoken-text overlays.\n\n"
        "== LAYOUT: RELATIVE POSITIONING ONLY ==\n"
        "Overlaps come from giving objects independent coordinates. Use Manim's relative layout "
        "API so positions follow from real object sizes:\n"
        "1. One absolute placement per region: build each beat's objects as ONE VGroup, lay it "
        "out, then place(group, region) once (regions: title, main, left, right, equation). "
        "Never call move_to/shift with numeric coordinates on individual labels or parts.\n"
        "2. Lay out groups with group.arrange(RIGHT|DOWN, buff=MED_SMALL_BUFF) or "
        "group.arrange_in_grid(rows=, cols=, buff=(h, v)); align edges with align_to(ref, LEFT).\n"
        "3. Put every label relative to its target: label.next_to(target, UP|DOWN|LEFT|RIGHT, "
        "buff=MED_SMALL_BUFF); a label for a line goes beside it, never centred on it. Use "
        "Brace(obj, DOWN) with brace.get_tex(...) or BraceLabel for spans.\n"
        "4. Rows/columns of values: Matrix, MathTable or Table (their v_buff/h_buff space the "
        "entries); highlight with SurroundingRectangle or table.add_highlighted_cell((r, c)).\n"
        "5. Labels on moving objects follow them: always_redraw(lambda: MathTex(...).next_to(obj, "
        "UP, buff=SMALL_BUFF)) or label.add_updater(lambda m: m.next_to(obj, UP)).\n"
        "6. Adding to a composition: rebuild the group with the new member and re-arrange it "
        "(e.g. VGroup(a, plus, b).arrange(RIGHT) via arrange_in_band), never drop the new object "
        "at a guessed position. Replacing: FadeOut(old) in the same play that introduces new.\n"
        "Readability: text at FONT sizes or larger (effective size >= 24 after any shrinking); "
        "show fewer objects rather than scaling down. Nothing below y=-2.8 (subtitles).\n"
        f"{math_guidance}\n\n"
        f"== MANIM CE API RULES ==\n{rules}\n\nAGENT SKILLS:\n{skill_context}\n"
    )
    template_block = (
        f"== WORKING TEMPLATE ({template.name}: {template.purpose}) ==\n"
        "This template renders cleanly and passes every layout check. Adapt it rather than "
        "starting from scratch: keep its structure (CueClock with CUES, place()/REGIONS, FONT "
        "sizes, arrange_in_band, persistent objects moved or transformed in place, temporaries "
        "removed in the same play that introduces the next visual) and replace its example "
        "content with this scene's content. Other visual types are fine when the narration needs "
        "them, built with the same helpers.\n"
        f"```python\n{template.source}```\n"
    )
    prompt = (
        "== NARRATION, ONE CUE PER SENTENCE ==\n"
        f"EXACT SPOKEN NARRATION (alignment only, never a text overlay):\n{spoken}\n"
        f"Cue plan (set CUES to exactly these {len(durations)} durations, in order):\n{cue_plan}\n"
        "For each cue: make the visual change that matches that sentence with clock.play(...), "
        "then call clock.next_cue(). End with clock.finish(). Use only clock.play, never "
        "self.play/self.wait directly, so the picture changes exactly when the voice does.\n"
        f"BEAT COUNT: write exactly {len(durations)} beats, one per cue, so clock.next_cue() "
        f"appears {max(0, len(durations) - 1)} time(s) and clock.finish() ends the last beat. "
        "If the storyboard lists more visual steps than cues, merge them into the cue whose "
        "sentence describes them. Any clock.play after the last cue is skipped, because the "
        "narration has already ended.\n"
        "On-screen STEM terms may be written 'English term (native term)'; copy such labels "
        "exactly, including both parts.\n"
        "NARRATION/VISUAL CONTRACT: the screen must show what the voice says at that moment, with "
        "the same example, symbols and numbers, in the same order. When the voice names a "
        "quantity, value or step, the matching object is visible or highlighted during that "
        "sentence. Never show a different example, invented values, or a topic the narration does "
        "not mention. Do not substitute an unrelated worked example.\n\n"
        f"{figure_guidance}{plot_guidance}"
        "== SCENE ==\n"
        f"Class: exactly `class {scene_class}(Scene):` (no other Scene class). "
        f"Title: {display_title!r} via place(SafeTitle(...), 'title'); never the whole request.\n"
        f"Visual plan and equations:\n{scene_payload}\n"
        "When lesson_closing is tagged, finish on the resolved takeaway picture and hold it to the "
        "last word; never fade to black while the tutor is still speaking.\n\n"
        + f"RELEVANT CORRECTIONS:\n{context}\nREPAIR FEEDBACK:\n{repairs}\n"
        + (
            "LEARNER'S EDIT REQUEST for this scene (the reason it is being redone; make the "
            f"picture answer it while still following the narration):\n{edit_request}\n"
            if edit_request
            else ""
        )
        + (
            "FAILED SOURCE TO REPAIR:\n"
            + previous_code
            + "\nRepair this actual source using the observed findings. Preserve correct content and "
            "timing; fix the root cause rather than rebuilding an unrelated scene. Check whether "
            "a referenced variable is actually used on screen. Matrix row indices increase downward: "
            "prefer Manim Matrix/MobjectMatrix or y=(middle-row)*UP, never a vertically flipped grid.\n"
            if previous_code
            else ""
        )
        + "Return only complete Python code."
    )
    if fallback_only or getattr(provider, "name", "") == "mock":
        raw_code = "SCHOLARMOTION_DETERMINISTIC_FALLBACK = True\n" + _fallback_scene_code(
            fallback_spec, scene_class, sentence_durations=sentence_durations
        )
    else:
        # Never substitute a canned topic template for model code: those
        # templates (a river-bank attention demo, a water-pipe Ohm's law) do
        # not match this scene's narration and produced videos whose picture
        # contradicted the voice. Retry the model, then fail the attempt.
        last_error: Exception | None = None
        raw_code = ""
        for attempt in range(3):
            try:
                raw_code = await _generate_with_cache(
                    provider,
                    prompt,
                    [shared_rules, template_block],
                    repair=bool(previous_code),
                    images=_figure_images(spec),
                )
                break
            except Exception as error:  # noqa: BLE001 - provider SDK errors vary
                last_error = error
                logger.warning(
                    "Code model failed for scene %s (attempt %d/3, %s: %r)",
                    spec.scene_id,
                    attempt + 1,
                    type(error).__name__,
                    error,
                )
                if attempt < 2:
                    await asyncio.sleep(CODE_RETRY_DELAY_SECONDS * (attempt + 1))
        if not raw_code:
            raise CodeGenerationError(
                f"code model unavailable for {spec.scene_id} after 3 attempts: "
                f"{type(last_error).__name__}: {last_error}"
            )
    # Pin the measured cue durations so animation and narration end together,
    # and accept a template-derived class that kept the template's name.
    raw_code = with_cues(raw_code, list(sentence_durations or []))
    raw_code = re.sub(r"\bclass TemplateScene\(", f"class {scene_class}(", raw_code)
    code = normalize_manim_direction_constants(
        raw_code, display_title=display_title, latex_enabled=latex_available()
    )
    return GeneratedSceneCode(
        scene_class=scene_class,
        python_code=code,
        assumptions=[],
        timing_markers={beat.beat_id: beat.start_seconds or 0 for beat in spec.visual_beats},
    )


def _figure_images(spec: SceneSpec) -> list[bytes]:
    """The paper figures with a coordinate grid, so highlight boxes land on target.

    Only the model's copy has the grid; the video shows the clean figure.
    """
    images = []
    for figure in spec.paper_figures:
        try:
            images.append(_with_fraction_grid(Path(figure.path)))
        except OSError:
            logger.warning("Paper figure %s is missing at %s", figure.label, figure.path)
    return images


def _with_fraction_grid(path: Path) -> bytes:
    """Overlay labelled lines at every 0.1 of width/height (fractions from top-left)."""
    from io import BytesIO

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - Pillow ships with Manim
        return path.read_bytes()
    raw = path.read_bytes()
    try:
        image = Image.open(BytesIO(raw)).convert("RGB")
    except Exception:  # noqa: BLE001 - an unreadable image is sent unchanged
        return raw
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    font = ImageFont.load_default(size=max(16, min(width, height) // 28))
    for step in range(1, 10):
        x, y = round(width * step / 10), round(height * step / 10)
        draw.line([(x, 0), (x, height)], fill=(255, 0, 160, 110), width=2)
        draw.line([(0, y), (width, y)], fill=(255, 0, 160, 110), width=2)
        draw.text((x + 3, 2), f"{step / 10:.1f}", fill=(200, 0, 120, 255), font=font)
        draw.text((2, y + 2), f"{step / 10:.1f}", fill=(200, 0, 120, 255), font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _generate_with_cache(
    provider, prompt: str, prefix: list[str], *, repair: bool, images: list[bytes] | None = None
) -> str:
    """Send the shared prefix as cacheable blocks when the provider supports it."""
    try:
        parameters = inspect.signature(provider.generate_code).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "cached_prefix" in parameters:
        extra = {"images": images} if images and "images" in parameters else {}
        return await provider.generate_code(
            prompt, cached_prefix=prefix, purpose="code_repair" if repair else "code", **extra
        )
    return await provider.generate_code("\n".join([*prefix, prompt]))


def _short_scene_title(value: str) -> str:
    """A scene title short enough for the title band; the planner writes the wording."""
    title = " ".join(value.split())
    if len(title) > 48:
        title = title[:48].rsplit(" ", 1)[0]
    return title.strip(" .,:;-") or "A visual lesson"


def _visual_only_fallback(
    spec: SceneSpec, scene_class: str, sentence_durations: list[float] | None
) -> str:
    """Safe fallback: the scene's title and equations, timed to its narration.

    Used only when no model can write the scene. It shows nothing the scene
    spec does not already contain, so it can never contradict the narration.
    """
    title = json.dumps(_short_scene_title(spec.title), ensure_ascii=False)
    total = (
        sum(max(0.2, value) for value in sentence_durations or []) or spec.duration_target_seconds
    )
    lines = [_latex_to_unicode(value) for value in spec.equations if value.strip()][:4]
    items = ", ".join(
        f"Text({json.dumps(line, ensure_ascii=False)}, font_size=32, color=YELLOW)"
        for line in lines
    )
    reveal = (
        f"        equations = VGroup({items}).arrange(DOWN, buff=0.45).move_to(DOWN * 0.3)\n"
        "        equations.scale_to_fit_width(min(equations.width, 11))\n"
        "        self.play(LaggedStart(*(Write(line) for line in equations), lag_ratio=0.3), "
        f"run_time={min(2.4, max(0.6, total * 0.3))!r})\n"
        if lines
        else ""
    )
    spent = 0.8 + (min(2.4, max(0.6, total * 0.3)) if lines else 0.0)
    return f"""SCHOLARMOTION_GENERIC_PLACEHOLDER = True
from manim import *

class {scene_class}(Scene):
    def construct(self):
        title = Text({title}, font_size=34, color=WHITE).scale_to_fit_width(10).to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)
{reveal}        self.wait({max(0.2, total - spent)!r})
"""


def _fallback_scene_code(
    spec: SceneSpec,
    scene_class: str,
    *,
    sentence_durations: list[float] | None = None,
) -> str:
    """Build a safe offline scene when every model route is unavailable."""
    return _visual_only_fallback(spec, scene_class, sentence_durations)
