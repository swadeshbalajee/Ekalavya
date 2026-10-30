"""Move project folders into per-user storage: ``<new-root>/<owner_id>/<project_id>/``.

Moves each project's folder out of ``--old-root`` (flat ``<project_id>/`` or an
earlier per-user layout), creates folders for projects that have none yet, and
rewrites the absolute file paths stored in the database (columns and JSON
documents) and inside the
projects' JSON artifacts. It is a dry run unless ``--apply`` is given, and can
be rerun safely. Stop the API and worker first, then set OBJECT_STORAGE_ROOT
to the new root:

    python scripts/migrate_project_storage.py --old-root projects --new-root "C:/data/projects" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
from pathlib import Path

from sqlalchemy import text

from scholarmotion.config import get_settings
from scholarmotion.persistence import Database

ABSOLUTE = re.compile(r"^([A-Za-z]:)?[\\/]")


def relocate(value: object, owners: dict[str, str | None], new_root: Path) -> str | None:
    """The new location of an absolute path inside a project folder, or None if unchanged."""
    if not isinstance(value, str) or not ABSOLUTE.match(value):
        return None
    parts = re.split(r"[\\/]+", value)
    for index, part in enumerate(parts):
        if part in owners:
            folder = new_root / owners[part] / part if owners[part] else new_root / part
            moved = str(folder.joinpath(*parts[index + 1 :]))
            return moved if moved != value else None
    return None


def rewrite(value, owners, new_root):
    if isinstance(value, dict):
        return {key: rewrite(item, owners, new_root) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, owners, new_root) for item in value]
    return relocate(value, owners, new_root) or value


async def _is_jsonb(connection, table: str, column: str) -> bool:
    kind = await connection.scalar(
        text(
            "select data_type from information_schema.columns where table_schema = 'public' "
            "and table_name = :table and column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return kind == "jsonb"


def find_folder(roots: list[Path], project_id: str) -> Path | None:
    for root in roots:
        for candidate in [root / project_id, *root.glob(f"*/{project_id}")]:
            if candidate.is_dir():
                return candidate
    return None


async def main(old_root: Path, new_root: Path, apply: bool) -> None:
    database = Database(get_settings().database_url)
    async with database.engine.begin() as connection:
        owners = {
            str(project_id): str(owner_id) if owner_id else None
            for project_id, owner_id in await connection.execute(
                text("select id, owner_id from projects")
            )
        }
        columns = (
            await connection.execute(
                text(
                    "select table_name, column_name from information_schema.columns "
                    "where table_schema = 'public' and data_type in ('text', 'character varying') "
                    "and (column_name like '%uri' or column_name like '%path')"
                )
            )
        ).all()

        moved = created = 0
        for project_id, owner_id in owners.items():
            target = new_root / owner_id / project_id if owner_id else new_root / project_id
            source = find_folder([old_root, new_root], project_id)
            if source is not None and source.resolve() == target.resolve():
                continue
            if source is None:
                created += 1
                if apply:
                    target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                print(f"skip {project_id}: both {source} and {target} exist")
                continue
            moved += 1
            print(f"move {source} -> {target}")
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
        print(f"{moved} folders moved, {created} empty folders created")

        known = set(owners)
        if old_root.is_dir():
            orphans = [p.name for p in old_root.iterdir() if p.is_dir() and p.name not in known]
            if orphans and old_root.resolve() != new_root.resolve():
                print(f"{len(orphans)} folders in {old_root} have no project and were left there")

        rows = 0
        for table, column in columns:
            values = await connection.execute(
                text(f'select id, "{column}" from "{table}" where "{column}" is not null')
            )
            for row_id, value in values.all():
                new_value = relocate(value, owners, new_root)
                if new_value is None:
                    continue
                rows += 1
                if apply:
                    await connection.execute(
                        text(f'update "{table}" set "{column}" = :value where id = :id'),
                        {"value": new_value, "id": row_id},
                    )
        print(f"{rows} stored paths rewritten")

        # Paths inside JSON columns too, such as a scene plan's paper figures.
        json_columns = (
            await connection.execute(
                text(
                    "select c.table_name, c.column_name from information_schema.columns c "
                    "where c.table_schema = 'public' and c.data_type in ('json', 'jsonb') "
                    "and exists (select 1 from information_schema.columns i where "
                    "i.table_schema = 'public' and i.table_name = c.table_name "
                    "and i.column_name = 'id')"
                )
            )
        ).all()
        documents = 0
        for table, column in json_columns:
            values = await connection.execute(
                text(
                    f'select id, "{column}"::text from "{table}" '
                    f"where \"{column}\"::text like '%projects%'"
                )
            )
            for row_id, raw in values.all():
                data = json.loads(raw)
                updated = rewrite(data, owners, new_root)
                if updated == data:
                    continue
                documents += 1
                if apply:
                    await connection.execute(
                        text(
                            f'update "{table}" set "{column}" = cast(:value as '
                            f"{'jsonb' if await _is_jsonb(connection, table, column) else 'json'})"
                            " where id = :id"
                        ),
                        {"value": json.dumps(updated), "id": row_id},
                    )
        print(f"{documents} stored JSON documents rewritten")

        files = 0
        # A dry run leaves folders in place, so look where they are now too.
        roots = {new_root} if apply else {old_root, new_root}
        for path in (path for root in roots if root.is_dir() for path in root.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            updated = rewrite(data, owners, new_root)
            if updated != data:
                files += 1
                if apply:
                    path.write_text(json.dumps(updated, indent=2, default=str), encoding="utf-8")
        print(f"{files} JSON artifacts rewritten")
        if not apply:
            await connection.rollback()
            print("dry run: nothing changed; pass --apply to migrate")
    await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old-root", type=Path, default=Path("projects"))
    parser.add_argument("--new-root", type=Path, default=get_settings().object_storage_root)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.old_root.resolve(), args.new_root.resolve(), args.apply))
