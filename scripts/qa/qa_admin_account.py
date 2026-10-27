"""Use the requested UI account and transfer only this run's acceptance projects."""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from scholarmotion.config import get_settings
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import Project


async def main():
    settings = get_settings()
    path = Path(".local/qa/scenario-session.json")
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"projects": {}}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", headers={"X-API-Key": settings.app_api_key}, timeout=30
    ) as client:
        data = {"username": "admin", "password": os.environ["QA_ADMIN_PASSWORD"]}
        result = await client.post("/v1/auth/login", json=data)
        if result.status_code == 401:
            result = await client.post("/v1/auth/register", json={**data, "display_name": "Admin"})
        result.raise_for_status()
        account = result.json()
    database = Database(settings.database_url)
    async with database.sessions() as session:
        for project_id in state["projects"].values():
            project = await session.scalar(select(Project).where(Project.id == UUID(project_id)))
            if project is not None:
                project.owner_id = UUID(account["user"]["id"])
        await session.commit()
    await database.close()
    state["token"] = account["token"]
    state["username"] = "admin"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Admin UI account ready; {len(state['projects'])} acceptance projects visible.")


asyncio.run(main())
