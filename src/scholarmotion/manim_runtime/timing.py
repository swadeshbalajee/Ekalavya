"""Reconciles animation run time with the narration audio length."""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class TimingAdjustment:
    scale: float
    extra_wait_seconds: float
    requires_repair: bool
    reason: str


def reconcile_timing(
    estimated_seconds: float,
    actual_seconds: float,
    *,
    tolerance: float = 0.12,
    repair_threshold: float = 0.35,
) -> TimingAdjustment:
    if estimated_seconds <= 0 or actual_seconds <= 0:
        raise ValueError("durations must be positive")
    relative = abs(actual_seconds - estimated_seconds) / estimated_seconds
    if relative <= tolerance:
        return TimingAdjustment(
            1.0, max(0.0, actual_seconds - estimated_seconds), False, "within tolerance"
        )
    scale = actual_seconds / estimated_seconds
    if relative <= repair_threshold:
        return TimingAdjustment(
            min(1.3, max(0.75, scale)), 0.0, False, "retime animation run_time values"
        )
    return TimingAdjustment(
        1.0, 0.0, True, "audio duration differs too much; code timing repair required"
    )


def scale_manim_durations(code: str, scale: float) -> str:
    """Scale Scene.play/wait durations uniformly to match measured narration."""
    scale = min(2.5, max(0.4, scale))
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    class DurationScaler(ast.NodeTransformer):
        changed = False

        def visit_Call(self, node: ast.Call):
            self.generic_visit(node)
            if not isinstance(node.func, ast.Attribute):
                return node
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "self":
                return node
            factor = ast.Constant(value=scale)
            if node.func.attr == "play":
                runtime = next(
                    (keyword for keyword in node.keywords if keyword.arg == "run_time"), None
                )
                if runtime is None:
                    node.keywords.append(ast.keyword(arg="run_time", value=factor))
                elif isinstance(runtime.value, ast.Constant) and isinstance(
                    runtime.value.value, (int, float)
                ):
                    runtime.value = ast.Constant(value=runtime.value.value * scale)
                else:
                    runtime.value = ast.BinOp(left=runtime.value, op=ast.Mult(), right=factor)
                self.changed = True
            elif node.func.attr == "wait":
                if not node.args:
                    node.args.append(factor)
                elif isinstance(node.args[0], ast.Constant) and isinstance(
                    node.args[0].value, (int, float)
                ):
                    node.args[0] = ast.Constant(value=node.args[0].value * scale)
                else:
                    node.args[0] = ast.BinOp(left=node.args[0], op=ast.Mult(), right=factor)
                self.changed = True
            return node

    scaler = DurationScaler()
    tree = scaler.visit(tree)
    return ast.unparse(ast.fix_missing_locations(tree)) if scaler.changed else code
