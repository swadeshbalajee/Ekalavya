"""Per-learner knowledge graph: concepts, relations and the chats they came from.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _fk(name: str, table: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=nullable
    )


_TABLES = {
    "knowledge_nodes": lambda: [
        *_base(),
        _fk("owner_id", "user_accounts"),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=60), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "key"),
    ],
    "knowledge_edges": lambda: [
        *_base(),
        _fk("owner_id", "user_accounts"),
        _fk("project_id", "projects"),
        _fk("source_id", "knowledge_nodes"),
        _fk("target_id", "knowledge_nodes"),
        sa.Column("relation", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ],
    "knowledge_mentions": lambda: [
        *_base(),
        _fk("node_id", "knowledge_nodes"),
        _fk("project_id", "projects"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "project_id"),
    ],
}

_INDEXES = {
    "knowledge_nodes": ("owner_id",),
    "knowledge_edges": ("owner_id", "project_id", "source_id", "target_id"),
    "knowledge_mentions": ("node_id", "project_id"),
}


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("projects")}
    if "graph_synced_at" not in columns:
        with op.batch_alter_table("projects") as batch:
            batch.add_column(sa.Column("graph_synced_at", sa.DateTime(timezone=True)))
    inspector = sa.inspect(bind)
    for name, build in _TABLES.items():
        if not inspector.has_table(name):
            op.create_table(name, *build())
    inspector = sa.inspect(bind)
    for table, names in _INDEXES.items():
        present = {index["name"] for index in inspector.get_indexes(table)}
        for column in names:
            if f"ix_{table}_{column}" not in present:
                op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in reversed(list(_TABLES)):
        op.drop_table(table)
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("graph_synced_at")
