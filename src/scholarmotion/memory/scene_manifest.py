"""Per-scene manifest entries: order, status, dependencies, and artifact versions."""

from __future__ import annotations

from dataclasses import dataclass, field

from scholarmotion.agents.manager import Manager
from scholarmotion.schemas import SceneStatus


@dataclass
class SceneManifestEntry:
    scene_id: str
    chapter_id: str
    order: int
    status: SceneStatus = SceneStatus.PLANNED
    dependencies: list[str] = field(default_factory=list)
    versions: dict[str, int] = field(
        default_factory=lambda: {"spec": 1, "script": 1, "code": 0, "audio": 0, "render": 0}
    )
    duration: float = 0
    timeline_start: float | None = None
    timeline_end: float | None = None
    verification_state: str | None = None


class SceneManifest:
    def __init__(self):
        self.entries: dict[str, SceneManifestEntry] = {}
        self.manager = Manager()

    def add(self, entry: SceneManifestEntry) -> None:
        if entry.scene_id in self.entries:
            raise ValueError(f"duplicate scene: {entry.scene_id}")
        self.entries[entry.scene_id] = entry

    def transition(self, scene_id: str, target: SceneStatus) -> None:
        entry = self.entries[scene_id]
        entry.status = self.manager.transition(entry.status, target)
