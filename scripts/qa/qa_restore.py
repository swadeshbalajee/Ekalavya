"""Restore a delivered scene version after a failed localization."""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from scholarmotion.config import get_settings


async def main():
    root = Path(".local/qa")
    s = get_settings()
    state = json.loads((root / "scenario-session.json").read_text(encoding="utf-8"))
    p = state["projects"]["paper"]
    base = "/v1/projects/" + p
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-API-Key": s.app_api_key, "Authorization": "Bearer " + state["token"]},
        timeout=180,
        limits=httpx.Limits(keepalive_expiry=2),
    ) as c:
        before = (await c.get(base)).json()
        old = Path(before["video_path"])
        digest = hashlib.sha256(old.read_bytes()).hexdigest()
        r = await c.post(base + "/scenes/S01/restore/1")
        r.raise_for_status()
        after = (await c.get(base)).json()
        result = {
            "project_id": p,
            "response": r.json(),
            "before_status": before["status"],
            "after_status": after["status"],
            "old_video_immutable": hashlib.sha256(old.read_bytes()).hexdigest() == digest,
            "new_video_exists": Path(after["video_path"]).is_file(),
        }
        (root / "restore-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)


asyncio.run(main())
