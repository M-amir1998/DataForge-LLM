"""Deterministic, leakage-safe train/validation/test assignment.

The unit of assignment is a *group*, never a single document: near-duplicate clusters,
exact-duplicate groups, and identical texts always share a key (``Document.group_key``),
so a cluster can never straddle two splits. Assignment hashes the group key with a seeded
salt: it depends only on the key, so it is reproducible and stable when the corpus grows
(a document in ``test`` stays in ``test``). The trade-off is that realized ratios are
binomially distributed around the targets, which is noticeable only on tiny datasets.

Strategies:

* ``hash``   - group key (default);
* ``source`` - whole sources go to one split (held-out source evaluation);
* ``domain`` - whole domains go to one split;
* ``time``   - by the earliest timestamp in each group against ordered cutoffs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from dataforge.config.models import SplitConfig
from dataforge.domain.document import Document
from dataforge.domain.hashing import unit_interval


def parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


class Splitter:
    def __init__(self, config: SplitConfig) -> None:
        self.config = config
        self._names = list(config.ratios)
        self._edges: list[float] = []
        running = 0.0
        for name in self._names:
            running += config.ratios[name]
            self._edges.append(running)
        self._edges[-1] = 1.0
        self._cutoffs: list[tuple[dt.datetime, str]] = []
        for split, value in config.time_cutoffs.items():
            parsed = parse_timestamp(value)
            if parsed is None:
                raise ValueError(f"invalid time cutoff for split '{split}': {value!r}")
            self._cutoffs.append((parsed, split))
        self._cutoffs.sort()
        self._group_time: dict[str, dt.datetime] = {}

    @property
    def needs_observation(self) -> bool:
        return self.config.strategy == "time"

    @property
    def splits(self) -> list[str]:
        return list(self._names)

    def _by_hash(self, key: str) -> str:
        value = unit_interval(key, f"split:{self.config.seed}")
        for name, edge in zip(self._names, self._edges, strict=True):
            if value < edge:
                return name
        return self._names[-1]

    def key(self, document: Document) -> str:
        strategy = self.config.strategy
        if strategy == "source":
            return document.source_id
        if strategy == "domain":
            return document.metadata.domain or "general"
        return document.group_key()

    def observe(self, documents: Sequence[Document]) -> None:
        """Time strategy only: remember the earliest timestamp of every group."""
        for document in documents:
            timestamp = parse_timestamp(document.metadata.timestamp)
            if timestamp is None:
                continue
            key = document.group_key()
            current = self._group_time.get(key)
            if current is None or timestamp < current:
                self._group_time[key] = timestamp

    def group_times(self) -> dict[str, dt.datetime]:
        """Earliest timestamp per group observed so far (time strategy)."""
        return dict(self._group_time)

    def set_group_times(self, times: dict[str, dt.datetime]) -> None:
        self._group_time = dict(times)

    def assign(self, document: Document) -> str:
        cfg = self.config
        key = self.key(document)
        if key in cfg.assignments:
            return cfg.assignments[key]
        if cfg.strategy == "time":
            timestamp = self._group_time.get(document.group_key())
            if timestamp is None:
                return cfg.missing_timestamp_split
            split = self._names[0]
            for cutoff, name in self._cutoffs:
                if timestamp >= cutoff:
                    split = name
            return split
        return self._by_hash(key)
