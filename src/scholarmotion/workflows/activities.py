"""Temporal activities that run builds, edits, localization, and reindexing."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from temporalio import activity

from scholarmotion.api.service import (
    edit_range,
    keep_earlier_video,
    localize_project,
    run_generation,
)
from scholarmotion.config import get_settings
from scholarmotion.observability import record_workflow_terminal
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import Project, WorkflowExecution
from scholarmotion.services.index_reconciliation import reconcile_qdrant_index

logger = logging.getLogger(__name__)


async def _set_workflow_execution_status(
    settings, project_id: str, status: str, *, error: str | None = None
) -> None:
    try:
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        # Unit tests and direct local invocation do not have Temporal context.
        return
    database = Database(settings.database_url)
    try:
        async with database.sessions() as session:
            execution = await session.scalar(
                select(WorkflowExecution).where(
                    WorkflowExecution.workflow_id == workflow_id,
                    WorkflowExecution.project_id == UUID(project_id),
                )
            )
            if execution is None:
                return
            execution.status = status
            if status == "CANCELLED":
                project = await session.get(Project, UUID(project_id))
                if project is not None:
                    await keep_earlier_video(session, project, otherwise="CANCELLED")
            if error:
                execution.detail = {**(execution.detail or {}), "error": error[:2000]}
            await session.commit()
    except Exception:
        # A status write must not hide the original activity result/failure.
        logger.exception("could not persist Temporal workflow status %s", status)
    finally:
        await database.close()


@activity.defn(name="build_project_activity")
async def build_project_activity(project_id: str) -> dict:
    settings = get_settings()
    try:
        result = await run_generation(Database(settings.database_url), settings, UUID(project_id))
    except asyncio.CancelledError:
        await _set_workflow_execution_status(settings, project_id, "CANCELLED")
        record_workflow_terminal(workflow_type="BuildVideoWorkflow", status="CANCELLED")
        raise
    except Exception as error:
        await _set_workflow_execution_status(settings, project_id, "FAILED", error=str(error))
        record_workflow_terminal(workflow_type="BuildVideoWorkflow", status="FAILED")
        raise
    await _set_workflow_execution_status(settings, project_id, "COMPLETED")
    record_workflow_terminal(workflow_type="BuildVideoWorkflow", status="COMPLETED")
    return {"project_id": project_id, "video_path": result.video_path, "scenes": len(result.scenes)}


@activity.defn(name="edit_project_activity")
async def edit_project_activity(
    project_id: str, start: float, end: float, instruction: str
) -> dict:
    settings = get_settings()
    try:
        await edit_range(
            Database(settings.database_url), settings, UUID(project_id), start, end, instruction
        )
    except asyncio.CancelledError:
        await _set_workflow_execution_status(settings, project_id, "CANCELLED")
        record_workflow_terminal(workflow_type="EditProjectWorkflow", status="CANCELLED")
        raise
    except Exception as error:
        await _set_workflow_execution_status(settings, project_id, "FAILED", error=str(error))
        record_workflow_terminal(workflow_type="EditProjectWorkflow", status="FAILED")
        raise
    await _set_workflow_execution_status(settings, project_id, "COMPLETED")
    record_workflow_terminal(workflow_type="EditProjectWorkflow", status="COMPLETED")
    return {"project_id": project_id, "start": start, "end": end}


@activity.defn(name="localize_project_activity")
async def localize_project_activity(project_id: str, scene_key: str | None = None) -> dict:
    settings = get_settings()
    try:
        scenes = await localize_project(
            Database(settings.database_url), settings, UUID(project_id), scene_key=scene_key
        )
    except asyncio.CancelledError:
        await _set_workflow_execution_status(settings, project_id, "CANCELLED")
        record_workflow_terminal(workflow_type="LocalizeProjectWorkflow", status="CANCELLED")
        raise
    except Exception as error:
        await _set_workflow_execution_status(settings, project_id, "FAILED", error=str(error))
        record_workflow_terminal(workflow_type="LocalizeProjectWorkflow", status="FAILED")
        raise
    await _set_workflow_execution_status(settings, project_id, "COMPLETED")
    record_workflow_terminal(workflow_type="LocalizeProjectWorkflow", status="COMPLETED")
    return {"project_id": project_id, "scenes": scenes}


@activity.defn(name="reconcile_qdrant_activity")
async def reconcile_qdrant_activity() -> dict[str, int]:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.sessions() as session:
            result = await reconcile_qdrant_index(session, settings)
            await session.commit()
            return result
    finally:
        await database.close()
