"""Compare persisted English scene specifications with their original artifacts."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import Scene, SceneSpecification


async def main():
    s = get_settings()
    root = Path(".local/qa")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    db = Database(s.database_url)
    rows = []
    async with db.sessions() as session:
        pairs = (
            await session.execute(
                select(Scene, SceneSpecification)
                .join(SceneSpecification, SceneSpecification.scene_id == Scene.id)
                .where(Scene.project_id == UUID(state["projects"]["paper"]))
            )
        ).all()
        for scene, spec in pairs:
            path = Path(spec.uri)
            if not path.is_absolute():
                path = s.object_storage_root / path
            artifact = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            narration = spec.payload.get("narration", "")
            rows.append(
                {
                    "scene_id": scene.scene_key,
                    "version": spec.version,
                    "active": spec.active,
                    "matches_original_artifact": artifact == spec.payload,
                    "no_tamil_or_hindi_in_canonical_narration": not any(
                        "\u0900" <= ch <= "\u097f" or "\u0b80" <= ch <= "\u0bff" for ch in narration
                    ),
                    "equations": spec.payload.get("equations", []),
                }
            )
    await db.close()
    (root / "canonical-spec-results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(rows, ensure_ascii=False))


asyncio.run(main())
