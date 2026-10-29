"""Per-learner research settings and a running summary of each chat's older turns.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_COLUMNS = {
    "user_accounts": [sa.Column("research_settings", sa.JSON(), nullable=True)],
    "projects": [
        sa.Column("chat_summary", sa.Text(), nullable=True),
        sa.Column("chat_summary_until", sa.DateTime(timezone=True), nullable=True),
    ],
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in _COLUMNS.items():
        present = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column.name not in present:
                op.add_column(table, column)


def downgrade() -> None:
    for table, columns in _COLUMNS.items():
        for column in columns:
            op.drop_column(table, column.name)
