"""Callable tool facade supplied to agents alongside the progressive skill prompt."""

from __future__ import annotations

from scholarmotion.skills.service import LoadedSkill, SkillCatalogEntry, SkillService


class SkillTools:
    """Agent-facing skill discovery/loading operations."""

    def __init__(self, skills: SkillService):
        self.skills = skills

    async def skill_catalog(self) -> list[SkillCatalogEntry]:
        return await self.skills.catalog()

    async def skill_load(self, name: str) -> LoadedSkill:
        return await self.skills.load(name)

    async def skill_resource(self, name: str, path: str) -> str | None:
        return (await self.skills.resource(name, path)).content

    async def skill_create_draft(self, **payload) -> LoadedSkill:
        return await self.skills.create_draft(**payload)

    async def skill_activate(self, name: str, version: int) -> LoadedSkill:
        """Activate a reviewed draft version.

        The service keeps version history intact; callers should apply their
        own authorization/review policy before exposing this operation.
        """
        return await self.skills.activate(name, version)
