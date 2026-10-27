"""Cancel an active edit and verify the previous delivered media stays intact."""

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import WorkflowExecution


async def main():
    s = get_settings()
    root = Path(".local/qa")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    p = state["projects"]["paper"]
    base = "/v1/projects/" + p
    database = Database(s.database_url)
    async with database.sessions() as session:
        execution = await session.scalar(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.project_id == UUID(p),
                WorkflowExecution.status == "RUNNING",
            )
            .order_by(WorkflowExecution.created_at.desc())
        )
        is_edit = execution is not None and execution.workflow_type == "EditProjectWorkflow"
    await database.close()
    if not is_edit:
        print("Skipped: no active edit workflow; other workflow types are not cancelled.")
        return
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": s.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=90,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        project = (await c.get(base)).json()
        video = Path(project["video_path"])
        before = hashlib.sha256(video.read_bytes()).hexdigest()
        folder = s.object_storage_root / p / "scenes" / "S02"
        hashes = {
            str(f): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in folder.rglob("*")
            if f.is_file()
        }
        r = await c.post(base + "/cancel")
        print("Cancel request:", r.status_code, flush=True)
        if r.status_code == 409:
            result = {"project_id": p, "cancel_status": "ALREADY_TERMINAL", "http_status": 409}
            (root / "active-edit-cancellation.json").write_text(json.dumps(result, indent=2))
            return
        r.raise_for_status()
        progress = {}
        for _ in range(30):
            progress = (await c.get(base + "/progress")).json()
            if progress["status"] == "CANCELLED":
                break
            await asyncio.sleep(2)
        active = (await c.get(base)).json()
        delivered = await c.get(base + "/video")
        versions = (await c.get(base + "/scenes/S01/versions")).json()
        changed = [
            f
            for f, h in hashes.items()
            if not Path(f).exists() or hashlib.sha256(Path(f).read_bytes()).hexdigest() != h
        ]
        result = {
            "project_id": p,
            "cancel_status": progress.get("status"),
            "active_video_unchanged": active.get("video_path") == str(video)
            and hashlib.sha256(video.read_bytes()).hexdigest() == before,
            "video_http_status": delivered.status_code,
            "delivered_hash_unchanged": hashlib.sha256(delivered.content).hexdigest() == before,
            "untouched_files_checked": len(hashes),
            "changed_after_cancel": changed,
            "versions": versions,
        }
        (root / "active-edit-cancellation.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in result.items() if k != "versions"}), flush=True)


asyncio.run(main())
