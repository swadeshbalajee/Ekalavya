"""The language a chat replies in, separate from the language of its videos.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    present = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "chat_language" not in present:
        op.add_column(
            "projects",
            sa.Column("chat_language", sa.String(80), nullable=False, server_default="English"),
        )


def downgrade() -> None:
    op.drop_column("projects", "chat_language")
