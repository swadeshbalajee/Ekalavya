"""Give existing chats model-written titles.

Only chats whose title is still the start of their first message are renamed;
a title the learner chose is kept. Run inside the API container:

    docker compose exec api python scripts/retitle_chats.py
"""

import asyncio

from sqlalchemy import select

from scholarmotion.api.main import database, name_chat
from scholarmotion.persistence.models import Project


async def main() -> None:
    async with database.sessions() as session:
        projects = (await session.scalars(select(Project))).all()
    provisional = [
        project
        for project in projects
        if project.title.strip()
        in {(project.original_request or "").strip()[:80], (project.request or "").strip()[:80]}
    ]
    print(f"{len(provisional)} of {len(projects)} chats still have their first message as title")
    for project in provisional:
        await name_chat(project.id, project.owner_id, project.title)
    async with database.sessions() as session:
        for project in provisional:
            renamed = await session.get(Project, project.id)
            print(f"  {project.title[:50]!r} -> {renamed.title!r}")


if __name__ == "__main__":
    asyncio.run(main())
