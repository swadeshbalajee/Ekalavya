"""Object storage for media and artifacts (local disk or S3)."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> Path | None: ...


def safe_key(key: str) -> str:
    path = PurePosixPath(key.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe object key: {key!r}")
    return str(path)


def _project_id(segment: str) -> str | None:
    try:
        return segment if str(UUID(segment)) == segment else None
    except ValueError:
        return None


class LocalObjectStore:
    """Files on local disk, one folder per user: ``<root>/<owner_id>/<project_id>/``.

    Keys stay ``<project_id>/...``; the store finds the owner's folder, which
    ``assign_project`` creates when the project is created. A project with no
    owner folder (no owner, or created before per-user storage) stays at
    ``<root>/<project_id>/``.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._project_dirs: dict[str, Path] = {}

    def assign_project(self, project_id: UUID | str, owner_id: UUID | str | None) -> None:
        if owner_id is None:
            return
        folder = self.root / str(owner_id) / str(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        self._project_dirs[str(project_id)] = folder

    def _project_dir(self, project_id: str) -> Path:
        if project_id in self._project_dirs:
            return self._project_dirs[project_id]
        owned = next((path for path in self.root.glob(f"*/{project_id}") if path.is_dir()), None)
        if owned is None:
            return self.root / project_id
        self._project_dirs[project_id] = owned
        return owned

    def _path(self, key: str) -> Path:
        head, _, rest = safe_key(key).partition("/")
        project_id = _project_id(head)
        base = self._project_dir(project_id) if project_id else self.root / head
        candidate = (base / rest).resolve() if rest else base.resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("object escaped storage root")
        return candidate

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str:
        path = self._path(key)
        if path.exists() and not overwrite:
            raise FileExistsError(f"immutable artifact already exists: {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def put_versioned(self, prefix: str, suffix: str, data: bytes) -> tuple[int, str]:
        version = 1
        while self.exists(f"{prefix}/v{version}.{suffix}"):
            version += 1
        key = f"{prefix}/v{version}.{suffix}"
        return version, self.put_bytes(key, data)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def digest(self, key: str) -> str:
        return hashlib.sha256(self.get_bytes(key)).hexdigest()


class S3ObjectStore:
    def __init__(self, bucket: str, *, endpoint_url: str | None = None, **credentials: str):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[s3] for S3 storage") from exc
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, **credentials)

    def put_bytes(self, key: str, data: bytes, *, overwrite: bool = False) -> str:
        key = safe_key(key)
        if not overwrite and self.exists(key):
            raise FileExistsError(f"immutable artifact already exists: {key}")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=safe_key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=safe_key(key))
            return True
        except self.client.exceptions.ClientError:
            return False

    def local_path(self, key: str) -> None:
        return None
