"""Disk-space budgeting and guarding.

Before a run, :func:`estimate` sizes input, temporary materializations, and output, and
compares them with free space minus a configurable reserve. During a run,
:class:`DiskGuard` re-checks free space before every part commit, so a filling disk stops
the pipeline cleanly at a resumable checkpoint instead of corrupting state.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dataforge.errors import StorageBudgetExceeded

GIB: Final = 1024**3
#: Conservative zstd Parquet size relative to raw UTF-8 text for natural-language corpora.
PARQUET_RATIO: Final = 0.5


def disk_free_bytes(path: Path) -> int:
    """Free bytes on the filesystem holding ``path`` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


@dataclass(frozen=True, slots=True)
class DiskEstimate:
    input_bytes: int
    temp_bytes: int
    output_bytes: int
    free_bytes: int
    reserve_bytes: int

    @property
    def required_bytes(self) -> int:
        return self.temp_bytes + self.output_bytes

    @property
    def headroom_bytes(self) -> int:
        return self.free_bytes - self.reserve_bytes - self.required_bytes

    @property
    def ok(self) -> bool:
        return self.headroom_bytes >= 0


def estimate(
    input_bytes: int,
    materializations: int,
    work_dir: Path,
    reserve_bytes: int,
    *,
    keep_intermediates: bool,
) -> DiskEstimate:
    """Upper-bound disk use: every materialization stores at most the input size compressed.

    Without ``keep_intermediates`` at most two materializations coexist (the one being read
    and the one being written); with it, all of them persist for stage caching.
    """
    per_copy = int(input_bytes * PARQUET_RATIO)
    live_copies = materializations if keep_intermediates else min(materializations, 2)
    return DiskEstimate(
        input_bytes=input_bytes,
        temp_bytes=per_copy * live_copies,
        output_bytes=per_copy,
        free_bytes=disk_free_bytes(work_dir),
        reserve_bytes=reserve_bytes,
    )


class DiskGuard:
    def __init__(self, path: Path, reserve_bytes: int) -> None:
        self.path = path
        self.reserve_bytes = reserve_bytes

    def check(self, upcoming_bytes: int = 0) -> None:
        free = disk_free_bytes(self.path)
        if free - upcoming_bytes < self.reserve_bytes:
            raise StorageBudgetExceeded(
                f"free disk space would drop below the reserve ({free / GIB:.2f} GiB free, "
                f"{self.reserve_bytes / GIB:.2f} GiB reserved); the run stopped at a resumable checkpoint",
                required_bytes=upcoming_bytes + self.reserve_bytes,
                available_bytes=free,
            )
