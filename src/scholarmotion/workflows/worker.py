"""Temporal worker process."""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from scholarmotion.config import get_settings

from .activities import (
    build_project_activity,
    edit_project_activity,
    localize_project_activity,
    reconcile_qdrant_activity,
)
from .definitions import (
    BuildVideoWorkflow,
    EditProjectWorkflow,
    LocalizeProjectWorkflow,
    QdrantReconciliationWorkflow,
)


async def run_worker() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            BuildVideoWorkflow,
            EditProjectWorkflow,
            LocalizeProjectWorkflow,
            QdrantReconciliationWorkflow,
        ],
        activities=[
            build_project_activity,
            edit_project_activity,
            localize_project_activity,
            reconcile_qdrant_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
