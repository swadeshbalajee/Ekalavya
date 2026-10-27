"""User-owned project folders for organizing tutor conversations.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("project_folders"):
        op.create_table(
            "project_folders",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "owner_id",
                sa.Uuid(),
                sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", "name"),
        )
        op.create_index("ix_project_folders_owner_id", "project_folders", ["owner_id"])

    project_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "folder_id" not in project_columns:
        op.add_column("projects", sa.Column("folder_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_projects_folder_id_project_folders",
            "projects",
            "project_folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_projects_folder_id", "projects", ["folder_id"])


def downgrade() -> None:
    project_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "folder_id" in project_columns:
        op.drop_constraint("fk_projects_folder_id_project_folders", "projects", type_="foreignkey")
        op.drop_index("ix_projects_folder_id", table_name="projects")
        op.drop_column("projects", "folder_id")
    if sa.inspect(op.get_bind()).has_table("project_folders"):
        op.drop_table("project_folders")
