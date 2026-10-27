from __future__ import annotations

import logging
import re
from pathlib import Path

from scholarmotion.schemas import SceneSpec, VerificationIssue

logger = logging.getLogger(__name__)


def _frame_indexes(value) -> list[int]:
    """Normalize provider frame labels to zero-based schema indexes."""
    if not isinstance(value, list):
        return []
    indexes: list[int] = []
    for frame in value:
        if isinstance(frame, int) and not isinstance(frame, bool):
            indexes.append(frame)
            continue
        match = re.search(r"(?:frame[_ -]*)?(\d+)$", str(frame), re.IGNORECASE)
        if match:
            indexes.append(int(match.group(1)))
    return indexes


async def verify_visual(
    provider, spec: SceneSpec, frame_paths: list[str]
) -> list[VerificationIssue]:
    if not getattr(provider, "capabilities", None) or not provider.capabilities.images:
        return []
    images = [Path(path).read_bytes() for path in frame_paths if Path(path).exists()]
    try:
        result = await provider.analyze_images(
            "Check composition, readability, state, arrows, motion, and narration alignment. "
            'Return only JSON in this form: {"issues":[{"category":"vision.overlap",'
            '"severity":"low|medium|high|critical","confidence":0.8,'
            '"frames":[],"objects":[],"description":"what is wrong",'
            '"suggested_repair":"how to fix it"}]}. Use an empty issues array when the frames pass. '
            + spec.model_dump_json(),
            images,
        )
    except Exception as error:  # noqa: BLE001 - provider SDK error types vary
        # Image review is an additional check. Keep a valid render available
        # when a shared inference endpoint is rate-limited or unavailable; the
        # deterministic render, layout, math, and pedagogy checks still run.
        logger.warning(
            "Visual verification unavailable for scene %s; continuing with deterministic checks (%s)",
            spec.scene_id,
            error,
        )
        return []
    normalized = []
    for issue in result.get("issues", []):
        if not isinstance(issue, dict):
            continue
        issue = {**issue, "frames": _frame_indexes(issue.get("frames", []))}
        normalized.append(VerificationIssue(scene_id=spec.scene_id, **issue))
    return normalized
