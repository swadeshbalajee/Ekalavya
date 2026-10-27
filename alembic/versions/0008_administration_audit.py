"""Record privileged skill and correction changes.

Revision ID: 0008
Revises: 0007_project_organization
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007_project_organization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("administration_audit"):
        _create_table()
    indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("administration_audit")
    }
    for name in ("action", "target_type", "target_id"):
        index_name = f"ix_administration_audit_{name}"
        if index_name not in indexes:
            op.create_index(index_name, "administration_audit", [name])


def _create_table() -> None:
    op.create_table(
        "administration_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=320), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_index("ix_administration_audit_target_id", table_name="administration_audit")
    op.drop_index("ix_administration_audit_target_type", table_name="administration_audit")
    op.drop_index("ix_administration_audit_action", table_name="administration_audit")
    op.drop_table("administration_audit")
