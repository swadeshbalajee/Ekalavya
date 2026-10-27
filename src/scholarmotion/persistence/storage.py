"""Object storage for media and artifacts (local disk or S3)."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Protocol


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


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / safe_key(key)).resolve()
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
