"""Versioned capability and Agent Skills APIs."""

from __future__ import annotations

import base64
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.api.admin import require_admin
from scholarmotion.config import Settings
from scholarmotion.persistence.models import AdministrationAudit
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_code_provider,
    create_stt_provider,
    create_translation_provider,
    create_tts_provider,
    create_vision_provider,
)
from scholarmotion.schemas import (
    SceneCodeRequest,
    SkillCreateRequest,
    SkillVersionRequest,
    STTTranscriptionRequest,
    TranslationRequest,
    TranslationResponse,
    TTSSynthesisRequest,
    VerificationReport,
    VisualVerificationRequest,
)
from scholarmotion.skills import SkillService
from scholarmotion.translation import TranslationService
from scholarmotion.verification.visual import verify_visual


def create_capability_router(
    settings: Settings, store: LocalObjectStore, session_dependency: Callable
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["agent capabilities"])
    session_dep = Depends(session_dependency)

    @router.get("/capabilities")
    async def capabilities():
        return {
            "code": {"provider": settings.code_provider or settings.main_llm_provider},
            "vision": {"provider": settings.visual_llm_provider or "mock"},
            "tts": {"provider": settings.tts_provider},
            "stt": {"provider": settings.stt_provider},
            "translation": {
                "provider": settings.translation_provider or settings.main_llm_provider
            },
            "skills": {"loading": "progressive metadata -> SKILL.md -> resource"},
        }

    @router.get("/skills")
    async def list_skills(session: AsyncSession = session_dep):
        return [_catalog_item(item) for item in await SkillService(session).catalog()]

    @router.get("/skills/{name}")
    async def load_skill(name: str, session: AsyncSession = session_dep):
        try:
            return _loaded_item(await SkillService(session).load(name))
        except KeyError as error:
            raise HTTPException(404, "skill not found") from error

    @router.get("/skills/{name}/resources/{resource_path:path}")
    async def load_skill_resource(
        name: str, resource_path: str, session: AsyncSession = session_dep
    ):
        try:
            item = await SkillService(session).resource(name, resource_path)
        except KeyError as error:
            raise HTTPException(404, "skill resource not found") from error
        return {
            "path": item.resource_path,
            "media_type": item.media_type,
            "content": item.content,
            "uri": item.object_uri,
        }

    @router.post("/skills", status_code=201)
    async def create_skill(payload: SkillCreateRequest, session: AsyncSession = session_dep):
        try:
            skill = await SkillService(session).create_draft(
                name=payload.name,
                description=payload.description,
                instructions=payload.instructions,
                manifest={
                    "capabilities": payload.capabilities,
                    "allowed_tools": payload.allowed_tools,
                },
                resources=[resource.model_dump() for resource in payload.resources],
            )
            await session.commit()
            return _loaded_item(skill)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/skills/{name}/versions", status_code=201)
    async def create_skill_version(
        name: str,
        payload: SkillVersionRequest,
        session: AsyncSession = session_dep,
    ):
        try:
            skill = await SkillService(session).create_draft(
                name=name,
                description=payload.description,
                instructions=payload.instructions,
                manifest=payload.manifest,
                resources=[resource.model_dump() for resource in payload.resources],
            )
            await session.commit()
            return _loaded_item(skill)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/skills/{name}/versions/{version}/activate")
    async def activate_skill(
        name: str,
        version: int,
        session: AsyncSession = session_dep,
        admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ):
        require_admin(settings, admin_key)
        try:
            skill = await SkillService(session).activate(name, version)
            session.add(
                AdministrationAudit(
                    action="skill.activate",
                    target_type="skill_version",
                    target_id=f"{name}:v{version}",
                    detail={"name": name, "version": version},
                )
            )
            await session.commit()
            return _loaded_item(skill)
        except KeyError as error:
            raise HTTPException(404, "skill version not found") from error

    @router.post("/capabilities/code/generate")
    async def code_generate(payload: SceneCodeRequest, session: AsyncSession = session_dep):
        context = await SkillService(session).context_for("code")
        try:
            result = await generate_scene_code(
                create_code_provider(settings),
                payload.scene,
                payload.corrections,
                payload.repair_feedback,
                skill_context=context,
            )
        except (RuntimeError, ValueError) as error:
            raise HTTPException(503, str(error)) from error
        return result

    @router.post("/capabilities/vision/verify")
    async def vision_verify(payload: VisualVerificationRequest):
        images = [_decode_base64(item) for item in payload.images_base64]
        frame_root = store.local_path(f"capabilities/vision/{uuid4()}")
        frame_root.mkdir(parents=True, exist_ok=False)
        paths = []
        for index, image in enumerate(images):
            path = frame_root / f"frame_{index:02d}.png"
            path.write_bytes(image)
            paths.append(str(path))
        try:
            issues = await verify_visual(create_vision_provider(settings), payload.scene, paths)
        except (RuntimeError, ValueError) as error:
            raise HTTPException(503, str(error)) from error
        return VerificationReport(
            scene_id=payload.scene.scene_id,
            passed=not any(issue.severity in {"high", "critical"} for issue in issues),
            score=1.0 if not issues else max(0.0, 1.0 - 0.2 * len(issues)),
            issues=issues,
            layers={"vision": not issues},
        )

    @router.post("/capabilities/tts/synthesize")
    async def tts_synthesize(payload: TTSSynthesisRequest):
        path = store.local_path(f"capabilities/tts/{uuid4()}.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        return await create_tts_provider(settings).synthesize(
            payload.text, path, language=payload.language
        )

    @router.post("/capabilities/stt/transcribe")
    async def stt_transcribe(payload: STTTranscriptionRequest):
        path = store.local_path(f"capabilities/stt/{uuid4()}.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_decode_base64(payload.audio_base64))
        return await create_stt_provider(settings).transcribe(path, language=payload.language)

    @router.post("/capabilities/translation/translate")
    async def translate(payload: TranslationRequest):
        text = await TranslationService(create_translation_provider(settings)).translate(
            payload.text,
            source_language=payload.source_language,
            target_language=payload.target_language,
        )
        return TranslationResponse(
            text=text,
            source_language=payload.source_language,
            target_language=payload.target_language,
        )

    return router


def _decode_base64(value: str) -> bytes:
    encoded = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise HTTPException(422, "invalid base64 payload") from error
    if len(decoded) > 10 * 1024 * 1024:
        raise HTTPException(413, "capability payload exceeds 10 MB")
    return decoded


def _catalog_item(skill) -> dict:
    return {"name": skill.name, "description": skill.description, "version": skill.version}


def _loaded_item(skill) -> dict:
    return {
        **_catalog_item(skill),
        "skill_md": skill.as_skill_md(),
        "manifest": skill.manifest,
        "resources": skill.resources,
    }
