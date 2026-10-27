"""Governed skill creation action available to orchestration agents."""

from __future__ import annotations

from scholarmotion.schemas import SkillCreateRequest
from scholarmotion.skills import LoadedSkill, SkillService


class SkillAuthor:
    """Creates instruction-only skills through the same guardrails as the API."""

    def __init__(self, skills: SkillService):
        self.skills = skills

    async def create(self, request: SkillCreateRequest) -> LoadedSkill:
        return await self.skills.create_draft(
            name=request.name,
            description=request.description,
            instructions=request.instructions,
            manifest={"capabilities": request.capabilities, "allowed_tools": request.allowed_tools},
        )
