"""Exercise the complete migration chain, including fresh installs."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("preexisting_metadata", [False, True])
def test_upgrade_head_on_empty_or_orm_initialized_database(tmp_path, preexisting_metadata):
    db_path = tmp_path / "migration.db"
    url = "sqlite+aiosqlite:///" + db_path.as_posix()
    if preexisting_metadata:
        import asyncio

        from scholarmotion.persistence.database import Database

        async def initialize():
            database = Database(url)
            await database.create_all()
            await database.close()

        asyncio.run(initialize())
    env = {**os.environ, "DATABASE_URL": url}
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("select version_num from alembic_version").fetchone() == ("0011",)
        columns = {row[1] for row in connection.execute("pragma table_info(projects)")}
        assert {"pinned", "archived"} <= columns
        assert connection.execute(
            "select name from sqlite_master where type='table' and name='administration_audit'"
        ).fetchone()
