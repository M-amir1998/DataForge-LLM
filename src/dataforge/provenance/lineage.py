"""Code identity for lineage: package version, Git commit, and a content fingerprint.

The *code fingerprint* hashes every Python source file of the installed package, so it
identifies the exact code that produced a dataset even from a wheel install without Git.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import dataforge


@dataclass(frozen=True, slots=True)
class CodeIdentity:
    version: str
    git_sha: str | None
    git_dirty: bool | None
    fingerprint: str


def _git(args: list[str], cwd: Path) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, resolved git path
            [git, *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


#: Presentation-only code: it parses arguments and prints, so it cannot change outputs.
#: Run options that do (``--until``, forced stage modes) are part of stage fingerprints.
_PRESENTATION: tuple[str, ...] = ("cli/", "inspection.py", "__main__.py")


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    package_dir = Path(dataforge.__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir).as_posix()
        if relative.startswith(_PRESENTATION):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def code_identity() -> CodeIdentity:
    package_dir = Path(dataforge.__file__).resolve().parent
    sha = os.environ.get("DATAFORGE_GIT_SHA") or _git(["rev-parse", "HEAD"], package_dir)
    dirty: bool | None = None
    if sha is not None and "DATAFORGE_GIT_SHA" not in os.environ:
        status = _git(["status", "--porcelain", "--untracked-files=no", "--", "."], package_dir)
        dirty = bool(status) if status is not None else None
    return CodeIdentity(dataforge.__version__, sha, dirty, code_fingerprint())
