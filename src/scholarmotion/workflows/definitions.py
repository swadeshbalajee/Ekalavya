"""Temporal workflow definitions."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# These activities currently commit a whole project build/edit/localization and
# write immutable artifacts. Retrying the complete activity after a failure
# would replay partial side effects and collide with those artifact versions.
# Provider adapters and the scene repair loop handle transient failures inside
# the activity; until the workflow is split into smaller idempotent activities,
# leave whole-project activity retry to an explicit user retry.
NO_AUTOMATIC_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn
class BuildVideoWorkflow:
    """Durable project workflow; scene work remains bounded by pipeline settings."""

    @workflow.run
    async def run(self, project_id: str) -> dict:
        return await workflow.execute_activity(
            "build_project_activity",
            project_id,
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_AUTOMATIC_RETRY,
        )


@workflow.defn
class EditProjectWorkflow:
    @workflow.run
    async def run(self, project_id: str, start: float, end: float, instruction: str) -> dict:
        return await workflow.execute_activity(
            "edit_project_activity",
            args=[project_id, start, end, instruction],
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_AUTOMATIC_RETRY,
        )


@workflow.defn
class LocalizeProjectWorkflow:
    """Regenerate learner-facing text/audio from canonical English artifacts."""

    @workflow.run
    async def run(self, project_id: str, scene_key: str | None = None) -> dict:
        return await workflow.execute_activity(
            "localize_project_activity",
            args=[project_id, scene_key],
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=NO_AUTOMATIC_RETRY,
        )


@workflow.defn
class QdrantReconciliationWorkflow:
    """Periodically repair the rebuildable index from PostgreSQL records."""

    @workflow.run
    async def run(self, interval_seconds: int = 3600) -> dict[str, int]:
        interval_seconds = max(60, interval_seconds)
        while True:
            await workflow.execute_activity(
                "reconcile_qdrant_activity",
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=10),
                    maximum_interval=timedelta(minutes=5),
                    maximum_attempts=5,
                ),
            )
            await workflow.sleep(timedelta(seconds=interval_seconds))
