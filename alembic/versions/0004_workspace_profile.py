"""Persist local workspace profile and default presentation preferences.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("workspace_profiles"):
        return
    op.create_table(
        "workspace_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profile_key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("avatar_color", sa.String(length=20), nullable=False),
        sa.Column("default_duration_minutes", sa.Float(), nullable=False),
        sa.Column("default_text_language", sa.String(length=80), nullable=False),
        sa.Column("default_voice_language", sa.String(length=80), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_key"),
    )
    op.create_index("ix_workspace_profiles_profile_key", "workspace_profiles", ["profile_key"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("workspace_profiles"):
        op.drop_table("workspace_profiles")
