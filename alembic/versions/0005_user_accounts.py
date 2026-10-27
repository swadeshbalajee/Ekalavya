"""PostgreSQL-backed user accounts, login sessions, and project ownership.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("user_accounts"):
        op.create_table(
            "user_accounts",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("username", sa.String(length=80), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("avatar_color", sa.String(length=20), nullable=False, server_default="#10a37f"),
            sa.Column("default_duration_minutes", sa.Float(), nullable=False, server_default="3"),
            sa.Column("default_text_language", sa.String(length=80), nullable=False, server_default="English"),
            sa.Column("default_voice_language", sa.String(length=80), nullable=False, server_default="English"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("username"),
        )
        op.create_index("ix_user_accounts_username", "user_accounts", ["username"])
        op.create_index("ix_user_accounts_active", "user_accounts", ["active"])

    project_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "owner_id" not in project_columns:
        op.add_column("projects", sa.Column("owner_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_projects_owner_id_user_accounts",
            "projects",
            "user_accounts",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    if not sa.inspect(op.get_bind()).has_table("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
        op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
        op.create_index("ix_user_sessions_revoked", "user_sessions", ["revoked"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("user_sessions"):
        op.drop_table("user_sessions")
    project_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "owner_id" in project_columns:
        op.drop_constraint("fk_projects_owner_id_user_accounts", "projects", type_="foreignkey")
        op.drop_index("ix_projects_owner_id", table_name="projects")
        op.drop_column("projects", "owner_id")
    if sa.inspect(op.get_bind()).has_table("user_accounts"):
        op.drop_table("user_accounts")
