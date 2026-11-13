"""Deterministic, root-confined file discovery."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dataforge.config.loader import resolve_path
from dataforge.errors import SourceNotFound, UnsafePathError

_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: Path
    relative: str
    size: int


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob with ``**`` support into an anchored regex.

    Patterns without a ``/`` match at any depth (``*.py`` == ``**/*.py``).
    """
    if "/" not in pattern:
        pattern = f"**/{pattern}"
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1 : end].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(relative_posix: str, pattern: str) -> bool:
    return _glob_regex(pattern).match(relative_posix) is not None


def _matches(relative: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    if include and not any(glob_match(relative, p) for p in include):
        return False
    return not any(glob_match(relative, p) for p in exclude)


def display_path(path: Path, base_dir: Path, root: Path) -> str:
    """Provenance-friendly POSIX path: relative to ``base_dir`` when possible, never absolute."""
    for anchor in (base_dir, root.parent):
        try:
            return path.relative_to(anchor).as_posix()
        except ValueError:
            continue
    return path.name


def ensure_within(path: Path, root: Path) -> Path:
    """Resolve ``path`` and require it to stay inside ``root`` (blocks symlink escapes)."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise UnsafePathError(f"path escapes its source root: {path.name}")
    return resolved


def _walk(root: Path, skip_dirs: frozenset[str]) -> Iterable[Path]:
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for name in sorted(filenames):
            yield Path(current) / name


def discover_files(
    base_dir: Path,
    path_spec: str,
    *,
    include: Sequence[str] = ("**/*",),
    exclude: Sequence[str] = (),
    skip_dirs: Iterable[str] = (),
) -> list[SourceFile]:
    """List the files a source refers to, sorted by relative path for determinism.

    ``path_spec`` may be a file, a directory (walked recursively), or a glob relative to
    ``base_dir``.

    Raises:
        SourceNotFound: if nothing matches.
        UnsafePathError: if a symlink resolves outside the source root.
    """
    skip = frozenset(skip_dirs)
    files: list[SourceFile] = []
    if any(char in path_spec for char in _GLOB_CHARS):
        root = base_dir
        candidates: Iterable[Path] = sorted(p for p in base_dir.glob(path_spec) if p.is_file())
    else:
        target = resolve_path(base_dir, path_spec)
        if not target.exists():
            raise SourceNotFound(f"source path does not exist: {path_spec}")
        if target.is_file():
            return [SourceFile(target, display_path(target, base_dir, target.parent), target.stat().st_size)]
        root = target
        candidates = _walk(target, skip)

    for candidate in candidates:
        relative_to_root = candidate.relative_to(root).as_posix()
        if any(part in skip for part in Path(relative_to_root).parts[:-1]):
            continue
        if not _matches(relative_to_root, include, exclude):
            continue
        if candidate.is_symlink():
            ensure_within(candidate, root)
        files.append(SourceFile(candidate, display_path(candidate, base_dir, root), candidate.stat().st_size))
    if not files:
        raise SourceNotFound(f"no files matched source path '{path_spec}'")
    files.sort(key=lambda f: f.relative)
    return files
