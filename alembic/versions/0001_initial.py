"""Initial ScholarMotion schema.

Revision ID: 0001
"""

from alembic import op
from scholarmotion.persistence import models  # noqa: F401
from scholarmotion.persistence.database import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_source_chunks_fts "
            "ON source_chunks USING gin (to_tsvector('english', text))"
        )


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
