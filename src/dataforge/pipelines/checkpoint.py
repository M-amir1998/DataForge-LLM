"""Segment checkpoints: an atomically replaced progress file per segment directory.

Commit protocol for each checkpoint (crash-safe at every step):

1. rename the part's ``.tmp`` file to its final name;
2. write the part's artifact files and stateful-stage snapshots;
3. atomically replace ``_progress.json`` with the new checkpoint appended.

On restart, anything not referenced by ``_progress.json`` is deleted and processing resumes
from the last recorded input cursor.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

PROGRESS_FILE: Final = "_progress.json"
SUCCESS_FILE: Final = "_SUCCESS"
FORMAT_VERSION: Final = 1


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    atomic_write_text(path, lines)


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@dataclass(slots=True)
class Checkpoint:
    index: int
    part: str | None
    documents: int
    cursor: int
    stats: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "part": self.part,
            "documents": self.documents,
            "cursor": self.cursor,
            "stats": self.stats,
        }


@dataclass(slots=True)
class SegmentProgress:
    fingerprint: str
    checkpoints: list[Checkpoint] = field(default_factory=list)
    complete: bool = False
    barrier_summary: dict[str, Any] | None = None

    @property
    def cursor(self) -> int:
        return self.checkpoints[-1].cursor if self.checkpoints else 0

    @property
    def documents(self) -> int:
        return sum(c.documents for c in self.checkpoints)

    def part_names(self) -> list[str]:
        return [c.part for c in self.checkpoints if c.part]

    def save(self, directory: Path) -> None:
        payload = {
            "format": FORMAT_VERSION,
            "fingerprint": self.fingerprint,
            "complete": self.complete,
            "barrier_summary": self.barrier_summary,
            "checkpoints": [c.as_dict() for c in self.checkpoints],
        }
        atomic_write_text(directory / PROGRESS_FILE, json.dumps(payload, indent=1, sort_keys=True))

    @classmethod
    def load(cls, directory: Path, fingerprint: str) -> SegmentProgress | None:
        path = directory / PROGRESS_FILE
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("format") != FORMAT_VERSION or payload.get("fingerprint") != fingerprint:
            return None
        checkpoints = [
            Checkpoint(
                int(c["index"]),
                c.get("part"),
                int(c["documents"]),
                int(c["cursor"]),
                dict(c["stats"]),
            )
            for c in payload.get("checkpoints", [])
        ]
        return cls(fingerprint, checkpoints, bool(payload.get("complete")), payload.get("barrier_summary"))


def prune_unrecorded(directory: Path, progress: SegmentProgress) -> int:
    """Delete parts, artifacts, and snapshots written after the last recorded checkpoint."""
    removed = 0
    recorded = set(progress.part_names())
    next_index = len(progress.checkpoints)
    parts_dir = directory / "parts"
    if parts_dir.is_dir():
        for path in parts_dir.iterdir():
            if path.name not in recorded:
                path.unlink()
                removed += 1
    artifacts_dir = directory / "artifacts"
    if artifacts_dir.is_dir():
        for path in artifacts_dir.rglob("part-*.jsonl"):
            index = int(path.stem.split("-")[1])
            if index >= next_index:
                path.unlink()
                removed += 1
    state_dir = directory / "state"
    if state_dir.is_dir():
        for path in state_dir.iterdir():
            index = int(path.stem.rsplit("-", 1)[1])
            if index != next_index - 1:
                path.unlink()
                removed += 1
    return removed


def reset_directory(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
