"""Snapshot current acceptance state and media without copying credentials."""

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import SourceDocument, WorkflowExecution


async def main():
    settings = get_settings()
    root = Path(".local/qa")
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    result = {}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": settings.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=60,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        for name, p in state["projects"].items():
            base = "/v1/projects/" + p
            data = {}
            for endpoint in ("", "/progress", "/scenes", "/chat", "/timeline"):
                r = await c.get(base + endpoint)
                data[endpoint or "project"] = {"http_status": r.status_code, "data": r.json()}
            path = data["project"]["data"].get("video_path")
            if path and Path(path).exists():
                ffprobe = shutil.which("ffprobe")
                if ffprobe:
                    probe = await asyncio.to_thread(
                        subprocess.run,
                        [
                            ffprobe,
                            "-v",
                            "error",
                            "-show_streams",
                            "-show_format",
                            "-of",
                            "json",
                            path,
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=30,
                    )
                    data["ffprobe"] = json.loads(probe.stdout)
                for resource in ("video", "subtitles.vtt", "chapters.vtt"):
                    r = await c.get(base + "/" + resource)
                    data["http_" + resource] = {
                        "status": r.status_code,
                        "content_type": r.headers.get("content-type"),
                        "bytes": len(r.content),
                    }
                r = await c.get(base + "/video", headers={"Range": "bytes=0-1023"})
                data["http_range"] = {
                    "status": r.status_code,
                    "bytes": len(r.content),
                    "content_range": r.headers.get("content-range"),
                }
                for ext in ("srt", "vtt"):
                    subtitle = Path(path).parent / f"subtitles.{ext}"
                    if subtitle.exists():
                        text = subtitle.read_text(encoding="utf-8")
                        data[ext] = {
                            "time_ranges": re.findall(
                                r"(\d{2}:\d{2}:\d{2}[.,]\d{3}) --> (\d{2}:\d{2}:\d{2}[.,]\d{3})",
                                text,
                            ),
                            "bytes": subtitle.stat().st_size,
                        }
            result[name] = data
    database = Database(settings.database_url)
    async with database.sessions() as session:
        for name, p in state["projects"].items():
            executions = (
                await session.scalars(
                    select(WorkflowExecution).where(WorkflowExecution.project_id == UUID(p))
                )
            ).all()
            sources = (
                await session.scalars(
                    select(SourceDocument).where(SourceDocument.project_id == UUID(p))
                )
            ).all()
            result[name]["workflows"] = [
                {"workflow_id": e.workflow_id, "status": e.status, "detail": e.detail}
                for e in executions
            ]
            result[name]["sources"] = [
                {"title": s.title, "metadata": s.metadata_json} for s in sources
            ]
    await database.close()
    (root / "acceptance-snapshot.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    for name, data in result.items():
        print(
            name,
            data["project"]["data"]["status"],
            len(data["/scenes"]["data"]),
            "scenes",
            data.get("ffprobe", {}).get("format", {}).get("duration", ""),
            flush=True,
        )


asyncio.run(main())
