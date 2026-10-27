from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import WorkflowExecutionStatus

from scholarmotion.api import main as api_main
from scholarmotion.config import Settings
from scholarmotion.persistence.database import Base
from scholarmotion.persistence.models import Project, UserAccount, WorkflowExecution
from scholarmotion.workflows import activities


@pytest.mark.asyncio
async def test_temporal_activity_persists_terminal_workflow_status(monkeypatch, tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'activity-status.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    workflow_id = "project:test:build:123"
    async with sessions() as session:
        user = UserAccount(
            username="workflow-status-tester",
            password_hash="unused",
            display_name="Workflow Status Tester",
        )
        session.add(user)
        await session.flush()
        project = Project(
            id=project_id,
            owner_id=user.id,
            title="Workflow status test",
            request="A status test.",
        )
        session.add(project)
        session.add(
            WorkflowExecution(
                project_id=project_id,
                workflow_id=workflow_id,
                workflow_type="BuildVideoWorkflow",
                status="RUNNING",
            )
        )
        await session.commit()

    monkeypatch.setattr(
        activities.activity, "info", lambda: SimpleNamespace(workflow_id=workflow_id)
    )
    settings = Settings(database_url=database_url)
    await activities._set_workflow_execution_status(
        settings, str(project_id), "FAILED", error="render crashed"
    )

    async with sessions() as session:
        execution = await session.scalar(
            select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id)
        )
        assert execution.status == "FAILED"
        assert execution.detail["error"] == "render crashed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_progress_reconciles_temporal_cancellation(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def cancelled(_settings, _workflow_id):
        return WorkflowExecutionStatus.CANCELED

    monkeypatch.setattr(api_main, "get_workflow_status", cancelled)
    async with sessions() as session:
        user = UserAccount(
            username="cancelled-progress-tester",
            password_hash="unused",
            display_name="Cancellation Progress Tester",
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            title="Cancelled build",
            request="A cancellation test.",
            status="CANCELLING",
        )
        session.add(project)
        await session.flush()
        execution = WorkflowExecution(
            project_id=project.id,
            workflow_id="project:test:cancel:123",
            workflow_type="BuildVideoWorkflow",
            status="CANCELLING",
        )
        session.add(execution)
        await session.commit()

        context = api_main.current_user_id.set(user.id)
        try:
            result = await api_main.progress(project.id, session)
        finally:
            api_main.current_user_id.reset(context)

        assert result["status"] == "CANCELLED"
        assert project.status == "CANCELLED"
        assert execution.status == "CANCELLED"
    await engine.dispose()
