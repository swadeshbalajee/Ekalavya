"""Make a project a Chat, Video, or Both conversation.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "mode" not in columns:
        op.add_column(
            "projects",
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="both"),
        )
        op.create_index("ix_projects_mode", "projects", ["mode"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "mode" in columns:
        op.drop_index("ix_projects_mode", table_name="projects")
        op.drop_column("projects", "mode")
