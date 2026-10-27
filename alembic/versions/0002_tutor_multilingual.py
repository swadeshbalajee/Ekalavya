"""Tutor conversation and presentation-language fields.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("projects")
    if "original_request" not in columns:
        op.add_column("projects", sa.Column("original_request", sa.Text(), nullable=True))
    if "text_language" not in columns:
        op.add_column(
            "projects", sa.Column("text_language", sa.String(length=80), nullable=False, server_default="English")
        )
    if "voice_language" not in columns:
        op.add_column(
            "projects", sa.Column("voice_language", sa.String(length=80), nullable=False, server_default="English")
        )
    if not sa.inspect(op.get_bind()).has_table("tutor_messages"):
        op.create_table(
            "tutor_messages",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("canonical_text", sa.Text(), nullable=False),
            sa.Column("display_text", sa.Text(), nullable=False),
            sa.Column("language", sa.String(length=80), nullable=False, server_default="English"),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("action", sa.String(length=50), nullable=True),
        )
        op.create_index("ix_tutor_messages_project_id", "tutor_messages", ["project_id"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("tutor_messages"):
        op.drop_table("tutor_messages")
    columns = _columns("projects")
    for column in ("voice_language", "text_language", "original_request"):
        if column in columns:
            op.drop_column("projects", column)
