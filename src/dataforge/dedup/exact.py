"""Exact document deduplication on normalized-content hashes.

Memory is ~60 bytes per document (two uint64 hash halves, length, quality, id), independent
of document size; grouping is a single vectorized lexsort.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dataforge.config.models import DuplicatePolicy
from dataforge.dedup.policy import representative_keys
from dataforge.domain.document import Document
from dataforge.domain.hashing import content_hash, hex_to_uint64_pair

BoolArray = npt.NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class ExactDedupResult:
    keep: BoolArray
    in_group: BoolArray
    documents: int
    duplicates: int
    duplicate_bytes: int
    groups: int
    largest_group: int

    def save(self, path: Path) -> None:
        stats = np.array(
            [
                self.documents,
                self.duplicates,
                self.duplicate_bytes,
                self.groups,
                self.largest_group,
            ],
            dtype=np.int64,
        )
        np.savez(path, keep=self.keep, in_group=self.in_group, stats=stats)

    @classmethod
    def load(cls, path: Path) -> ExactDedupResult:
        with np.load(path, allow_pickle=False) as data:
            stats = [int(v) for v in data["stats"]]
            return cls(data["keep"].copy(), data["in_group"].copy(), *stats)

    def summary(self) -> dict[str, int]:
        return {
            "documents_before": self.documents,
            "documents_after": self.documents - self.duplicates,
            "duplicates": self.duplicates,
            "duplicate_bytes": self.duplicate_bytes,
            "duplicate_groups": self.groups,
            "largest_group": self.largest_group,
        }


class ExactDeduplicator:
    """Collects compact per-document keys, then decides which copy of each group survives."""

    def __init__(self, policy: DuplicatePolicy = "keep_first") -> None:
        self.policy = policy
        self._reset()

    def _reset(self) -> None:
        self._hi: list[npt.NDArray[np.uint64]] = []
        self._lo: list[npt.NDArray[np.uint64]] = []
        self._lengths: list[npt.NDArray[np.int64]] = []
        self._quality: list[npt.NDArray[np.float32]] = []
        self._ids: list[npt.NDArray[np.bytes_]] = []

    def reset(self) -> None:
        self._reset()

    def observe(self, documents: Sequence[Document]) -> None:
        if not documents:
            return
        his, los = [], []
        for doc in documents:
            digest = doc.metadata.content_hash or content_hash(doc.text)
            hi, lo = hex_to_uint64_pair(digest)
            his.append(hi)
            los.append(lo)
        self._hi.append(np.array(his, dtype=np.uint64))
        self._lo.append(np.array(los, dtype=np.uint64))
        self._lengths.append(np.array([d.num_bytes for d in documents], dtype=np.int64))
        self._quality.append(
            np.array(
                [d.metadata.quality_score if d.metadata.quality_score is not None else np.nan for d in documents],
                dtype=np.float32,
            )
        )
        self._ids.append(np.array([d.document_id for d in documents], dtype="S32"))

    def _concat(self) -> tuple[npt.NDArray[Any], ...]:
        def cat(parts: list[npt.NDArray[Any]], dtype: Any) -> npt.NDArray[Any]:
            return np.concatenate(parts) if parts else np.empty(0, dtype=dtype)

        return (
            cat(self._hi, np.uint64),
            cat(self._lo, np.uint64),
            cat(self._lengths, np.int64),
            cat(self._quality, np.float32),
            cat(self._ids, "S32"),
        )

    def finalize(self) -> tuple[ExactDedupResult, list[dict[str, Any]]]:
        """Return the decision arrays and one report row per removed duplicate."""
        hi, lo, lengths, quality, ids = self._concat()
        count = hi.shape[0]
        ordinals = np.arange(count, dtype=np.int64)
        keep = np.ones(count, dtype=bool)
        in_group = np.zeros(count, dtype=bool)
        rows: list[dict[str, Any]] = []
        if count == 0:
            return ExactDedupResult(keep, in_group, 0, 0, 0, 0, 0), rows

        keys = representative_keys(self.policy, ordinals, lengths, quality)
        order = np.lexsort((*keys, lo, hi))
        sorted_hi, sorted_lo = hi[order], lo[order]
        new_group = np.ones(count, dtype=bool)
        new_group[1:] = (sorted_hi[1:] != sorted_hi[:-1]) | (sorted_lo[1:] != sorted_lo[:-1])
        group_index = np.cumsum(new_group) - 1
        sizes = np.bincount(group_index)
        member_sizes = sizes[group_index]
        in_group[order] = member_sizes > 1
        keep[order] = new_group  # first of each group (by policy order) survives

        starts = np.flatnonzero(new_group)
        representative_sorted = order[starts[group_index]]
        representative = np.empty(count, dtype=np.int64)
        representative[order] = representative_sorted  # inverse permutation: doc -> its keeper
        removed = np.flatnonzero(~keep)
        for index in removed.tolist():
            rep = int(representative[index])
            rows.append(
                {
                    "content_hash": f"{int(hi[index]):016x}{int(lo[index]):016x}",
                    "document_id": ids[index].decode("ascii"),
                    "kept_document_id": ids[rep].decode("ascii"),
                    "bytes": int(lengths[index]),
                }
            )
        multi = sizes[sizes > 1]
        result = ExactDedupResult(
            keep=keep,
            in_group=in_group,
            documents=count,
            duplicates=int(removed.shape[0]),
            duplicate_bytes=int(lengths[removed].sum()),
            groups=int(multi.shape[0]),
            largest_group=int(np.max(multi)) if multi.shape[0] else 0,
        )
        return result, rows
