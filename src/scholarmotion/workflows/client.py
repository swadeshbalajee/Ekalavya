"""Starts, cancels, and queries Temporal workflows."""

from __future__ import annotations

from uuid import UUID, uuid4

from temporalio.client import Client, WorkflowExecutionStatus

from scholarmotion.config import Settings

from .definitions import BuildVideoWorkflow, EditProjectWorkflow, LocalizeProjectWorkflow


async def _client(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def start_build_workflow(settings: Settings, project_id: UUID) -> tuple[str, str]:
    # A project can be regenerated after a failed or completed attempt. Temporal
    # workflow IDs are unique within a namespace, so give every build its own ID.
    workflow_id = f"project:{project_id}:build:{uuid4()}"
    handle = await (await _client(settings)).start_workflow(
        BuildVideoWorkflow.run,
        str(project_id),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return workflow_id, handle.result_run_id


async def start_edit_workflow(
    settings: Settings, project_id: UUID, start: float, end: float, instruction: str
) -> tuple[str, str]:
    # Repeating the same edit is a new user operation; deterministic IDs caused
    # Temporal to reject a retry after the first workflow had already completed.
    workflow_id = f"project:{project_id}:edit:{uuid4()}"
    handle = await (await _client(settings)).start_workflow(
        EditProjectWorkflow.run,
        args=[str(project_id), start, end, instruction],
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return workflow_id, handle.result_run_id


async def cancel_workflow(settings: Settings, workflow_id: str) -> None:
    await (await _client(settings)).get_workflow_handle(workflow_id).cancel()


async def get_workflow_status(settings: Settings, workflow_id: str) -> WorkflowExecutionStatus:
    """Read Temporal's terminal state when an activity could not persist it."""
    description = await (await _client(settings)).get_workflow_handle(workflow_id).describe()
    return description.status


async def start_localize_workflow(
    settings: Settings, project_id: UUID, scene_key: str | None = None
) -> tuple[str, str]:
    workflow_id = f"project:{project_id}:localize:{uuid4()}"
    handle = await (await _client(settings)).start_workflow(
        LocalizeProjectWorkflow.run,
        args=[str(project_id), scene_key],
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return workflow_id, handle.result_run_id
