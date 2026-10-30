from __future__ import annotations

from scholarmotion.schemas import Timeline, TimelineSegment


def media_duration(audio_seconds: float, video_seconds: float | None) -> float:
    """A muxed scene occupies the duration of its longer audio/video stream."""
    return max(audio_seconds, video_seconds or 0.0)


def build_timeline(items: list[tuple[str, float, int, str]]) -> Timeline:
    cursor = 0.0
    segments = []
    for scene_id, duration, render_version, render_path in items:
        segments.append(
            TimelineSegment(
                scene_id=scene_id,
                start=cursor,
                end=cursor + duration,
                render_version=render_version,
                render_path=render_path,
            )
        )
        cursor += duration
    return Timeline(video_id="active", scenes=segments, duration_seconds=cursor)


def resolve_scenes(timeline: Timeline, start: float, end: float) -> list[TimelineSegment]:
    return [item for item in timeline.scenes if item.end > start and item.start < end]
