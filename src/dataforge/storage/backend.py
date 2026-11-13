"""Storage port and adapters.

Computation always happens on local disk (memory maps, Parquet writers, DuckDB). Finished
datasets are *published* through a :class:`StorageBackend`:

* :class:`LocalStorage` publishes with a single atomic directory rename;
* :class:`FsspecStorage` targets any fsspec filesystem (``s3://`` with the ``s3`` extra,
  MinIO, ``memory://`` in tests). Object stores have no atomic rename, so files are uploaded
  first and ``manifest.json`` last: a dataset without a manifest is incomplete by definition.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol

from dataforge.errors import DatasetExistsError, OptionalDependencyMissing, StorageError

MANIFEST_NAME = "manifest.json"


class StorageBackend(Protocol):
    def uri(self, relative: str) -> str: ...

    def exists(self, relative: str) -> bool: ...

    def read_bytes(self, relative: str) -> bytes: ...

    def write_bytes(self, relative: str, data: bytes) -> None: ...

    def publish_directory(self, local_dir: Path, relative: str) -> str: ...

    def list(self, relative: str) -> list[str]: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise StorageError(f"path escapes storage root: {relative}")
        return path

    def uri(self, relative: str) -> str:
        return self._path(relative).as_uri()

    def exists(self, relative: str) -> bool:
        return self._path(relative).exists()

    def read_bytes(self, relative: str) -> bytes:
        return self._path(relative).read_bytes()

    def write_bytes(self, relative: str, data: bytes) -> None:
        target = self._path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)

    def publish_directory(self, local_dir: Path, relative: str) -> str:
        target = self._path(relative)
        if target.exists():
            raise DatasetExistsError(f"refusing to overwrite immutable dataset at {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            local_dir.replace(target)
        except OSError:
            # Cross-device staging: copy, then commit by moving the manifest last.
            shutil.copytree(local_dir, target, ignore=shutil.ignore_patterns(MANIFEST_NAME))
            shutil.copy2(local_dir / MANIFEST_NAME, target / MANIFEST_NAME)
            shutil.rmtree(local_dir)
        return str(target)

    def list(self, relative: str) -> list[str]:
        base = self._path(relative)
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root.resolve()).as_posix() for p in base.rglob("*") if p.is_file())


class FsspecStorage:
    """Any fsspec filesystem addressed by URL, e.g. ``s3://bucket/datasets``."""

    def __init__(self, url: str, **storage_options: Any) -> None:
        try:
            import fsspec  # noqa: PLC0415 - only object-storage publishing needs it
        except ImportError as exc:  # pragma: no cover - fsspec is a core dependency
            raise OptionalDependencyMissing("object storage", "s3") from exc
        protocol, _, path = url.partition("://")
        if not path:
            raise StorageError(f"expected a URL like s3://bucket/prefix, got {url!r}")
        try:
            self.fs = fsspec.filesystem(protocol, **storage_options)
        except (ImportError, ValueError) as exc:
            raise OptionalDependencyMissing(f"'{protocol}://' storage", "s3") from exc
        self.protocol = protocol
        self.base = path.rstrip("/")

    def _key(self, relative: str) -> str:
        if ".." in Path(relative).parts:
            raise StorageError(f"path escapes storage root: {relative}")
        return f"{self.base}/{relative}".rstrip("/")

    def uri(self, relative: str) -> str:
        return f"{self.protocol}://{self._key(relative)}"

    def exists(self, relative: str) -> bool:
        return bool(self.fs.exists(self._key(relative)))

    def read_bytes(self, relative: str) -> bytes:
        data: bytes = self.fs.cat_file(self._key(relative))
        return data

    def write_bytes(self, relative: str, data: bytes) -> None:
        self.fs.pipe_file(self._key(relative), data)

    def publish_directory(self, local_dir: Path, relative: str) -> str:
        if self.exists(f"{relative}/{MANIFEST_NAME}"):
            raise DatasetExistsError(f"refusing to overwrite immutable dataset at {relative}")
        files = sorted(p for p in local_dir.rglob("*") if p.is_file() and p.name != MANIFEST_NAME)
        for file in files:
            self.fs.put_file(str(file), self._key(f"{relative}/{file.relative_to(local_dir).as_posix()}"))
        self.fs.put_file(str(local_dir / MANIFEST_NAME), self._key(f"{relative}/{MANIFEST_NAME}"))
        shutil.rmtree(local_dir)
        return self.uri(relative)

    def list(self, relative: str) -> list[str]:
        prefix = self._key(relative)
        if not self.fs.exists(prefix):
            return []
        found = self.fs.find(prefix)
        return sorted(str(item)[len(self.base) + 1 :] for item in found)


def storage_for(location: str, base_dir: Path) -> StorageBackend:
    """Pick a backend from a location string (URL -> fsspec, otherwise local path)."""
    if "://" in location:
        return FsspecStorage(location)
    path = Path(location)
    return LocalStorage(path if path.is_absolute() else (base_dir / path).resolve())
