"""CLI entry point that starts a Qdrant reconciliation workflow."""

from __future__ import annotations

import argparse
import asyncio

from temporalio.client import Client

from scholarmotion.config import get_settings

from .definitions import QdrantReconciliationWorkflow


async def start_reconciliation(interval_seconds: int) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = await client.start_workflow(
        QdrantReconciliationWorkflow.run,
        interval_seconds,
        id="scholarmotion-qdrant-reconciliation",
        task_queue=settings.temporal_task_queue,
    )
    print(f"Started Qdrant reconciliation workflow: {handle.id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Start periodic Qdrant index reconciliation")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()
    asyncio.run(start_reconciliation(args.interval_seconds))


if __name__ == "__main__":
    main()
