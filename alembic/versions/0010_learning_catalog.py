"""Public video catalog, library, notes, playlists, study plans and quizzes.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_PROJECT_COLUMNS = (
    sa.Column("visibility", sa.String(length=20), nullable=False, server_default="private"),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("subject", sa.String(length=120), nullable=True),
    sa.Column("tags", sa.JSON(), nullable=True),
    sa.Column("level", sa.String(length=80), nullable=True),
    sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("catalog_embedding", sa.JSON(), nullable=True),
)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _user(name: str = "user_id") -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )


def _project(nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        "project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete=ondelete), nullable=nullable
    )


def _updated() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)


_TABLES = {
    "saved_videos": lambda: [
        *_base(),
        _user(),
        _project(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id"),
    ],
    "watch_progress": lambda: [
        *_base(),
        _user(),
        _project(),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        _updated(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id"),
    ],
    "video_notes": lambda: [
        *_base(),
        _user(),
        _project(),
        sa.Column("at_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _updated(),
        sa.PrimaryKeyConstraint("id"),
    ],
    "playlists": lambda: [
        *_base(),
        _user("owner_id"),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _updated(),
        sa.PrimaryKeyConstraint("id"),
    ],
    "playlist_items": lambda: [
        *_base(),
        sa.Column(
            "playlist_id",
            sa.Uuid(),
            sa.ForeignKey("playlists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _project(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("playlist_id", "project_id"),
    ],
    "study_plans": lambda: [
        *_base(),
        _user("owner_id"),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minutes_per_day", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        _updated(),
        sa.PrimaryKeyConstraint("id"),
    ],
    "study_tasks": lambda: [
        *_base(),
        sa.Column(
            "plan_id",
            sa.Uuid(),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=False),
        _project(nullable=True, ondelete="SET NULL"),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ],
    "quizzes": lambda: [
        *_base(),
        _user("owner_id"),
        _project(nullable=True, ondelete="SET NULL"),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("language", sa.String(length=80), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ],
    "quiz_attempts": lambda: [
        *_base(),
        sa.Column(
            "quiz_id", sa.Uuid(), sa.ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False
        ),
        _user(),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ],
}

_INDEXES = {
    "projects": ("visibility", "subject"),
    "saved_videos": ("user_id", "project_id"),
    "watch_progress": ("user_id", "project_id"),
    "video_notes": ("user_id", "project_id"),
    "playlists": ("owner_id",),
    "playlist_items": ("playlist_id", "project_id"),
    "study_plans": ("owner_id", "status"),
    "study_tasks": ("plan_id", "due_date", "project_id"),
    "quizzes": ("owner_id", "project_id"),
    "quiz_attempts": ("quiz_id", "user_id"),
}


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("projects")}
    with op.batch_alter_table("projects") as batch:
        for column in _PROJECT_COLUMNS:
            if column.name not in existing:
                batch.add_column(column.copy())
    inspector = sa.inspect(bind)
    for name, columns in _TABLES.items():
        if not inspector.has_table(name):
            op.create_table(name, *columns())
    inspector = sa.inspect(bind)
    for table, columns in _INDEXES.items():
        present = {index["name"] for index in inspector.get_indexes(table)}
        for column in columns:
            if f"ix_{table}_{column}" not in present:
                op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, columns in reversed(_INDEXES.items()):
        if table == "projects":
            continue
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    for name in reversed(list(_TABLES)):
        op.drop_table(name)
    for column in ("visibility", "subject"):
        op.drop_index(f"ix_projects_{column}", table_name="projects")
    with op.batch_alter_table("projects") as batch:
        for column in reversed(_PROJECT_COLUMNS):
            batch.drop_column(column.name)
