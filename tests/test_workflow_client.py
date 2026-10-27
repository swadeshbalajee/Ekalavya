from types import SimpleNamespace
from uuid import uuid4

import pytest

from scholarmotion.config import Settings
from scholarmotion.workflows import client as workflow_client


@pytest.mark.asyncio
async def test_repeating_same_edit_starts_a_distinct_temporal_workflow(monkeypatch):
    started = []

    class FakeTemporalClient:
        async def start_workflow(self, *_args, **kwargs):
            started.append(kwargs["id"])
            return SimpleNamespace(result_run_id=f"run-{len(started)}")

    async def fake_client(_settings):
        return FakeTemporalClient()

    monkeypatch.setattr(workflow_client, "_client", fake_client)
    project_id = uuid4()
    arguments = (
        Settings(),
        project_id,
        0.0,
        31.0,
        "Regenerate this scene while preserving its content.",
    )

    first = await workflow_client.start_edit_workflow(*arguments)
    second = await workflow_client.start_edit_workflow(*arguments)

    assert first[0] != second[0]
    assert first[1] == "run-1"
    assert second[1] == "run-2"
