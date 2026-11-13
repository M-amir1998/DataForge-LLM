"""Safe, in-place archive reading.

Archives are streamed member by member and **never extracted to disk**, which removes the
zip-slip / tar path-traversal class of bugs entirely. Member names are still validated
because they become provenance locators. Decompression bombs are bounded by per-member,
total-size, member-count, and compression-ratio limits.
"""

from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from dataforge.errors import SourceFormatError, UnsafePathError

_ARCHIVE_SUFFIXES: Final = (".zip", ".tar", ".tar.gz", ".tgz")


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int = 100_000
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_compression_ratio: float = 200.0


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    data: bytes | None
    skipped_reason: str | None = None


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(_ARCHIVE_SUFFIXES)


def validate_member_name(name: str) -> str:
    """Reject absolute, drive-qualified, or parent-traversing member names."""
    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise UnsafePathError(f"absolute path in archive member: {name!r}")
    if any(part == ".." for part in posix.parts):
        raise UnsafePathError(f"parent traversal in archive member: {name!r}")
    return posix.as_posix()


def _check_totals(count: int, total: int, limits: ArchiveLimits) -> None:
    if count > limits.max_members:
        raise SourceFormatError(f"archive has more than {limits.max_members} members")
    if total > limits.max_total_bytes:
        raise SourceFormatError(f"archive expands beyond {limits.max_total_bytes} bytes")


def _iter_zip(path: Path, limits: ArchiveLimits) -> Iterator[ArchiveMember]:
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = sorted((i for i in archive.infolist() if not i.is_dir()), key=lambda i: i.filename)
        for count, info in enumerate(infos, start=1):
            name = validate_member_name(info.filename)
            _check_totals(count, total + info.file_size, limits)
            if info.file_size > limits.max_member_bytes:
                yield ArchiveMember(name, None, "member_too_large")
                continue
            if info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
                yield ArchiveMember(name, None, "suspicious_compression_ratio")
                continue
            with archive.open(info) as handle:
                # Never trust the declared size: read at most one byte past the limit.
                data = handle.read(limits.max_member_bytes + 1)
            if len(data) > limits.max_member_bytes:
                yield ArchiveMember(name, None, "member_too_large")
                continue
            total += len(data)
            _check_totals(count, total, limits)
            yield ArchiveMember(name, data)


def _iter_tar(path: Path, limits: ArchiveLimits) -> Iterator[ArchiveMember]:
    total = 0
    with tarfile.open(path, mode="r:*") as archive:
        members = sorted(archive.getmembers(), key=lambda m: m.name)
        count = 0
        for member in members:
            if member.isdir():
                continue
            count += 1
            name = validate_member_name(member.name)
            if not member.isfile():
                # Symlinks, hard links, devices and FIFOs are never followed.
                yield ArchiveMember(name, None, "non_regular_member")
                continue
            _check_totals(count, total + member.size, limits)
            if member.size > limits.max_member_bytes:
                yield ArchiveMember(name, None, "member_too_large")
                continue
            handle = archive.extractfile(member)
            if handle is None:
                yield ArchiveMember(name, None, "unreadable_member")
                continue
            with handle:
                data = handle.read(limits.max_member_bytes + 1)
            if len(data) > limits.max_member_bytes:
                yield ArchiveMember(name, None, "member_too_large")
                continue
            total += len(data)
            _check_totals(count, total, limits)
            yield ArchiveMember(name, data)


def iter_archive_members(path: Path, limits: ArchiveLimits | None = None) -> Iterator[ArchiveMember]:
    """Yield regular-file members in name order with their bytes (bounded by ``limits``)."""
    limits = limits or ArchiveLimits()
    try:
        if path.name.lower().endswith(".zip"):
            yield from _iter_zip(path, limits)
        else:
            yield from _iter_tar(path, limits)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as exc:
        raise SourceFormatError(f"corrupted archive {path.name}: {exc}") from exc
