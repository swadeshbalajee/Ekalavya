"""Database-backed Agent Skills with Claude-style progressive disclosure.

The database stores the logical equivalent of a skill directory: required
SKILL.md front matter/body plus optional `references/`, `assets/`, and
`scripts/` resources. Only the metadata catalog enters every agent prompt.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.persistence.models import AgentTool, Skill, SkillResource, SkillVersion

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESERVED = {"anthropic", "claude"}
_RESOURCE_PATH = re.compile(r"^(?:references|assets|scripts)/[a-zA-Z0-9._/-]+$")


@dataclass(frozen=True)
class SkillCatalogEntry:
    name: str
    description: str
    version: int


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    description: str
    version: int
    instructions: str
    manifest: dict[str, Any]
    resources: tuple[str, ...]

    def as_skill_md(self) -> str:
        return (
            f"---\nname: {self.name}\ndescription: {self.description}\n---\n\n{self.instructions}"
        )


class SkillService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def catalog(self) -> list[SkillCatalogEntry]:
        values = await self._active_versions()
        return [
            SkillCatalogEntry(skill.skill_key, version.description, version.version)
            for skill, version in values
        ]

    async def load(self, name: str) -> LoadedSkill:
        for skill, version in await self._active_versions():
            if skill.skill_key == name:
                return await self._loaded(skill, version)
        raise KeyError(name)

    async def resource(self, name: str, resource_path: str) -> SkillResource:
        skill = await self.load(name)
        version = await self.session.scalar(
            select(SkillVersion)
            .join(Skill)
            .where(Skill.skill_key == name, SkillVersion.version == skill.version)
        )
        resource = await self.session.scalar(
            select(SkillResource).where(
                SkillResource.skill_version_id == version.id,
                SkillResource.resource_path == resource_path,
            )
        )
        if resource is None:
            raise KeyError(resource_path)
        return resource

    async def context_for(self, capability: str) -> str:
        """First expose catalog metadata, then load only matching full skills."""
        catalog = await self.catalog()
        active = await self._active_versions()
        matching = [
            await self._loaded(skill, version)
            for skill, version in active
            if capability in version.manifest.get("capabilities", [])
        ]
        index = "\n".join(
            f"- {entry.name}: {entry.description} (v{entry.version})" for entry in catalog
        )
        loaded = "\n\n".join(
            f'<skill name="{skill.name}" version="{skill.version}">\n{skill.instructions}\n</skill>'
            for skill in matching
        )
        tools = "\n".join(
            f"- {tool.tool_key}: {tool.description}"
            for tool in (
                await self.session.scalars(
                    select(AgentTool)
                    .where(AgentTool.enabled.is_(True))
                    .order_by(AgentTool.tool_key)
                )
            ).all()
        )
        return (
            "<available_skills>\n"
            f"{index}\n"
            "Use the skills tool to load another skill or a declared resource only when its "
            "description matches the task.\n</available_skills>\n"
            f"<active_skills>\n{loaded}\n</active_skills>\n"
            f"<available_tools>\n{tools}\n</available_tools>"
        )

    async def create_draft(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        manifest: dict[str, Any] | None = None,
        resources: list[dict[str, str]] | None = None,
        created_by: str = "agent",
    ) -> LoadedSkill:
        self._validate(name, description, instructions, manifest or {}, resources or [])
        registered_tools = set(
            (
                await self.session.scalars(
                    select(AgentTool.tool_key).where(AgentTool.enabled.is_(True))
                )
            ).all()
        )
        unknown_tools = set((manifest or {}).get("allowed_tools", [])) - registered_tools
        if unknown_tools:
            raise ValueError(
                f"skill requests unregistered tools: {', '.join(sorted(unknown_tools))}"
            )
        skill = await self.session.scalar(select(Skill).where(Skill.skill_key == name))
        if skill is None:
            skill = Skill(skill_key=name, created_by=created_by, status="draft")
            self.session.add(skill)
            await self.session.flush()
            version_number = 1
        else:
            version_number = (
                await self.session.scalar(
                    select(SkillVersion.version)
                    .where(SkillVersion.skill_id == skill.id)
                    .order_by(SkillVersion.version.desc())
                    .limit(1)
                )
                or 0
            ) + 1
        checksum = _checksum(name, description, instructions, manifest or {})
        version = SkillVersion(
            skill_id=skill.id,
            version=version_number,
            description=description,
            instructions=instructions,
            manifest=manifest or {},
            checksum=checksum,
            status="draft",
        )
        self.session.add(version)
        await self.session.flush()
        for resource in resources or []:
            content = resource.get("content", "")
            self.session.add(
                SkillResource(
                    skill_version_id=version.id,
                    resource_path=resource["path"],
                    media_type=resource.get("media_type", "text/markdown"),
                    content=content,
                    checksum=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
        await self.session.flush()
        return await self._loaded(skill, version)

    async def activate(self, name: str, version_number: int) -> LoadedSkill:
        skill = await self.session.scalar(select(Skill).where(Skill.skill_key == name))
        version = (
            await self.session.scalar(
                select(SkillVersion).where(
                    SkillVersion.skill_id == skill.id, SkillVersion.version == version_number
                )
            )
            if skill
            else None
        )
        if skill is None or version is None:
            raise KeyError(name)
        await self.session.execute(
            update(SkillVersion)
            .where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.status == "active",
                SkillVersion.id != version.id,
            )
            .values(status="archived")
        )
        version.status = "active"
        skill.status = "active"
        skill.active_version = version_number
        return await self._loaded(skill, version)

    async def _active_versions(self) -> list[tuple[Skill, SkillVersion]]:
        values = (
            await self.session.execute(
                select(Skill, SkillVersion)
                .join(SkillVersion, SkillVersion.skill_id == Skill.id)
                .where(
                    Skill.status == "active",
                    SkillVersion.status == "active",
                    Skill.active_version == SkillVersion.version,
                )
                .order_by(Skill.skill_key)
            )
        ).all()
        return list(values)

    async def _loaded(self, skill: Skill, version: SkillVersion) -> LoadedSkill:
        resources = (
            await self.session.scalars(
                select(SkillResource.resource_path)
                .where(SkillResource.skill_version_id == version.id)
                .order_by(SkillResource.resource_path)
            )
        ).all()
        return LoadedSkill(
            skill.skill_key,
            version.description,
            version.version,
            version.instructions,
            version.manifest,
            tuple(resources),
        )

    @staticmethod
    def _validate(
        name: str,
        description: str,
        instructions: str,
        manifest: dict[str, Any],
        resources: list[dict[str, str]],
    ) -> None:
        if not _NAME.fullmatch(name) or len(name) > 64 or any(word in name for word in _RESERVED):
            raise ValueError(
                "skill name must be <=64 lowercase kebab-case characters without reserved words"
            )
        if not description or len(description) > 1024 or "<" in description or ">" in description:
            raise ValueError("description must be 1-1024 characters without XML tags")
        if not instructions.strip():
            raise ValueError("skill instructions are required")
        tools = manifest.get("allowed_tools", [])
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise ValueError("manifest.allowed_tools must be a list of registered tool names")
        for resource in resources:
            if not _RESOURCE_PATH.fullmatch(resource.get("path", "")):
                raise ValueError("skill resources must be below references/, assets/, or scripts/")


def _checksum(name: str, description: str, instructions: str, manifest: dict[str, Any]) -> str:
    data = f"{name}\n{description}\n{instructions}\n{sorted(manifest.items())}".encode()
    return hashlib.sha256(data).hexdigest()
