from __future__ import annotations

from scholarmotion.schemas import SceneSpec, VisualBeat
from scholarmotion.verification.visual import verify_visual


def _scene() -> SceneSpec:
    return SceneSpec(
        scene_id="S01",
        chapter_id="C01",
        title="Pythagoras theorem",
        learning_objective="Connect the side lengths of a right triangle.",
        duration_target_seconds=20,
        narration="The squares on the two shorter sides add to the square on the longest side.",
        visual_beats=[
            VisualBeat(beat_id="b1", narration_segment="Show a triangle", visual="Right triangle")
        ],
    )


async def test_rate_limited_vision_is_advisory_and_does_not_block_render(tmp_path):
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"frame")

    class RateLimitedVision:
        capabilities = type("Capabilities", (), {"images": True})()

        async def analyze_images(self, *_args, **_kwargs):
            error = RuntimeError("shared model pool is rate limited")
            error.status_code = 429
            raise error

    assert await verify_visual(RateLimitedVision(), _scene(), [str(image_path)]) == []


async def test_vision_frame_labels_are_normalized_before_schema_validation(tmp_path):
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"frame")

    class VisionWithNamedFrames:
        capabilities = type("Capabilities", (), {"images": True})()

        async def analyze_images(self, *_args, **_kwargs):
            return {
                "issues": [
                    {
                        "category": "vision.overlap",
                        "severity": "high",
                        "confidence": 0.9,
                        "frames": ["frame_0003", "frame 7", "unknown", 2],
                        "objects": [],
                        "description": "Text overlaps an arrow.",
                        "suggested_repair": "Move the text away from the arrow.",
                    }
                ]
            }

    issues = await verify_visual(VisionWithNamedFrames(), _scene(), [str(image_path)])

    assert len(issues) == 1
    assert issues[0].frames == [3, 7, 2]
