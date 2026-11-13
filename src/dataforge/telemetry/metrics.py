"""Process memory measurement and simple stopwatches.

Peak RSS comes from the OS where it is tracked (``peak_wset`` on Windows, ``ru_maxrss`` on
POSIX); a sampler also records the maximum RSS observed at batch boundaries, which works
everywhere and is what the bounded-memory benchmark reports.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import psutil

_PROCESS = psutil.Process()


def rss_bytes() -> int:
    return int(_PROCESS.memory_info().rss)


def os_peak_rss_bytes() -> int | None:
    info = _PROCESS.memory_info()
    peak = getattr(info, "peak_wset", None)
    if peak is not None:
        return int(peak)
    if sys.platform != "win32":
        import resource  # noqa: PLC0415 - POSIX-only module

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(usage) if sys.platform == "darwin" else int(usage) * 1024
    return None


@dataclass(slots=True)
class MemorySampler:
    """Tracks the maximum RSS observed across ``sample()`` calls."""

    peak_bytes: int = 0
    samples: int = 0

    def sample(self) -> int:
        current = rss_bytes()
        self.samples += 1
        self.peak_bytes = max(self.peak_bytes, current)
        return current


@dataclass(slots=True)
class Stopwatch:
    started: float = field(default_factory=time.perf_counter)

    def elapsed(self) -> float:
        return time.perf_counter() - self.started
