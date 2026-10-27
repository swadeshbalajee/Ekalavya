"""Check authoritative PDF persistence while the local Qdrant service is down."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import SourceDocument


async def main():
    s = get_settings()
    root = Path(".local/qa")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": s.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=120,
    ) as c:
        health = (await c.get("/corpus/health")).json()
        p = (
            await c.post(
                "/v1/projects",
                json={
                    "title": "QA Qdrant outage",
                    "request": "Discuss the attention paper.",
                    "mode": "chat",
                },
            )
        ).json()["id"]
        source = next(
            (s.object_storage_root / state["projects"]["paper"] / "sources").glob("*.pdf")
        )
        r = await c.post(
            f"/v1/projects/{p}/sources",
            files={"file": ("outage-paper.pdf", source.read_bytes(), "application/pdf")},
        )
    db = Database(s.database_url)
    async with db.sessions() as session:
        document = await session.scalar(
            select(SourceDocument).where(SourceDocument.project_id == UUID(p))
        )
        result = {
            "corpus_health": health,
            "upload_status": r.status_code,
            "project_id": p,
            "source_metadata": document.metadata_json if document else None,
        }
    await db.close()
    (root / "qdrant-outage-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        "Outage detected:",
        not health["reachable"],
        "PDF persisted:",
        r.status_code == 201,
        "vector retry pending:",
        result["source_metadata"].get("vector_status") == "pending",
        flush=True,
    )


asyncio.run(main())
