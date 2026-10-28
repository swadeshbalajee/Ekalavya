"""Add durable chat organization flags.

Revision ID: 0007_project_organization
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0007_project_organization"
down_revision = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The initial migration creates current ORM metadata on a fresh install.
    # Match the preceding migrations by also supporting already-present fields.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("projects")}
    indexes = {index["name"] for index in inspector.get_indexes("projects")}
    for name in ("pinned", "archived"):
        if name not in columns:
            op.add_column(
                "projects", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
            )
        index_name = f"ix_projects_{name}"
        if index_name not in indexes:
            op.create_index(index_name, "projects", [name])


def downgrade() -> None:
    op.drop_index("ix_projects_archived", table_name="projects")
    op.drop_index("ix_projects_pinned", table_name="projects")
    op.drop_column("projects", "archived")
    op.drop_column("projects", "pinned")
