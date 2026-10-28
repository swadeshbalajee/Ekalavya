"""Per-call usage records for the usage dashboard, backfilled from build summaries.

Revision ID: 0009
Revises: 0008
"""

import json
import uuid

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_INDEXES = ("user_id", "project_id", "run_id", "operation")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("usage_events"):
        op.create_table(
            "usage_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "project_id",
                sa.Uuid(),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("operation", sa.String(length=40), nullable=False),
            sa.Column("stage", sa.String(length=60), nullable=True),
            sa.Column("purpose", sa.String(length=120), nullable=False),
            sa.Column("scene_key", sa.String(length=40), nullable=True),
            sa.Column("provider", sa.String(length=60), nullable=True),
            sa.Column("model", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("calls", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("cached_tokens", sa.Integer(), nullable=False),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
            sa.Column("characters", sa.Integer(), nullable=False),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("usage_events")}
    for name in _INDEXES:
        if f"ix_usage_events_{name}" not in existing:
            op.create_index(f"ix_usage_events_{name}", "usage_events", [name])
    _backfill()


def _backfill() -> None:
    """Turn each earlier ``usage.summary`` progress event into aggregate rows."""
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM usage_events")).scalar():
        return
    rows = bind.execute(
        sa.text(
            "SELECT e.project_id, e.created_at, e.detail, p.owner_id FROM progress_events e "
            "JOIN projects p ON p.id = e.project_id WHERE e.event = 'usage.summary'"
        )
    ).all()
    usage = sa.table(
        "usage_events",
        *(
            sa.column(name)
            for name in (
                "id", "created_at", "user_id", "project_id", "run_id", "operation", "stage",
                "purpose", "scene_key", "provider", "model", "status", "calls", "input_tokens",
                "output_tokens", "cached_tokens", "reasoning_tokens", "characters", "cost_usd",
                "latency_ms",
            )
        ),
    )
    inserts = []
    for project_id, created_at, detail, owner_id in rows:
        summary = json.loads(detail) if isinstance(detail, str) else (detail or {})
        run_id = f"summary-{uuid.uuid4().hex[:12]}"
        for group in summary.get("by_purpose") or []:
            purpose = str(group.get("purpose") or "other")
            stage = purpose.split(":", 1)[0] if ":" in purpose else None
            inserts.append(
                {
                    "id": uuid.uuid4(),
                    "created_at": created_at,
                    "user_id": owner_id,
                    "project_id": project_id,
                    "run_id": run_id,
                    "operation": str(summary.get("operation") or "build"),
                    "stage": stage,
                    "purpose": purpose,
                    "scene_key": None,
                    "provider": "openrouter",
                    "model": str(group.get("model") or ""),
                    "status": "ok",
                    "calls": int(group.get("calls") or 1),
                    "input_tokens": int(group.get("input_tokens") or 0),
                    "output_tokens": int(group.get("output_tokens") or 0),
                    "cached_tokens": int(group.get("cached_tokens") or 0),
                    "reasoning_tokens": int(group.get("reasoning_tokens") or 0),
                    "characters": 0,
                    "cost_usd": group.get("cost_usd"),
                    "latency_ms": None,
                }
            )
    if inserts:
        op.bulk_insert(usage, inserts)


def downgrade() -> None:
    for name in reversed(_INDEXES):
        op.drop_index(f"ix_usage_events_{name}", table_name="usage_events")
    op.drop_table("usage_events")
