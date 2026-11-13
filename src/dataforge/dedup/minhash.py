"""Vectorized MinHash signatures and LSH banding.

Permutations are universal hashes ``(a * x + b) mod p`` with ``p = 2**32 - 5`` so that every
product fits in uint64 without overflow: ``(p - 1)**2 + p < 2**64``. Signatures are uint32.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Final

import numpy as np
import numpy.typing as npt

from dataforge.dedup.shingles import UInt64Array

UInt32Array = npt.NDArray[np.uint32]

PRIME: Final = np.uint64(4294967291)
EMPTY_VALUE: Final = np.uint32(0xFFFFFFFF)
#: Upper bound on shingles processed per vectorized chunk (bounds temporary memory).
_CHUNK_SHINGLES: Final = 16_384
_BAND_POLY: Final = np.uint64(0x9E3779B97F4A7C15)


class MinHasher:
    """Computes ``num_perm``-dimensional MinHash signatures from hashed shingles."""

    def __init__(self, num_perm: int = 128, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        self.num_perm = num_perm
        self._a = rng.integers(1, int(PRIME), size=num_perm, dtype=np.uint64)
        self._b = rng.integers(0, int(PRIME), size=num_perm, dtype=np.uint64)

    def _permute_min(self, hashes: UInt64Array) -> UInt32Array:
        reduced = hashes % PRIME
        values = (reduced[:, None] * self._a[None, :] + self._b[None, :]) % PRIME
        result: UInt32Array = values.min(axis=0).astype(np.uint32)
        return result

    def signature(self, hashes: UInt64Array) -> UInt32Array:
        """Signature of one document; an empty shingle set yields the sentinel signature."""
        if hashes.shape[0] == 0:
            return np.full(self.num_perm, EMPTY_VALUE, dtype=np.uint32)
        mins = np.full(self.num_perm, EMPTY_VALUE, dtype=np.uint32)
        for start in range(0, hashes.shape[0], _CHUNK_SHINGLES):
            mins = np.minimum(mins, self._permute_min(hashes[start : start + _CHUNK_SHINGLES]))
        return mins

    def signatures(self, batch: Sequence[UInt64Array]) -> UInt32Array:
        """Signatures for a batch, vectorized across documents within bounded chunks."""
        out = np.full((len(batch), self.num_perm), EMPTY_VALUE, dtype=np.uint32)
        pending: list[int] = []
        pending_size = 0

        def flush() -> None:
            nonlocal pending_size
            if not pending:
                return
            arrays = [batch[i] for i in pending]
            lengths = np.fromiter((a.shape[0] for a in arrays), dtype=np.int64, count=len(arrays))
            offsets = np.concatenate(([0], np.cumsum(lengths)[:-1]))
            stacked = np.concatenate(arrays) % PRIME
            values = (stacked[:, None] * self._a[None, :] + self._b[None, :]) % PRIME
            out[pending] = np.minimum.reduceat(values, offsets, axis=0).astype(np.uint32)
            pending.clear()
            pending_size = 0

        for index, hashes in enumerate(batch):
            size = hashes.shape[0]
            if size == 0:
                continue
            if size > _CHUNK_SHINGLES:
                out[index] = self.signature(hashes)
                continue
            if pending_size + size > _CHUNK_SHINGLES:
                flush()
            pending.append(index)
            pending_size += size
        flush()
        return out


def jaccard_estimate(left: UInt32Array, right: UInt32Array) -> float:
    return float(np.mean(left == right))


def candidate_probability(similarity: float, bands: int, rows: int) -> float:
    """Probability that a pair with Jaccard ``similarity`` shares at least one LSH bucket."""
    return float(1.0 - (1.0 - similarity**rows) ** bands)


def _false_positive_area(threshold: float, bands: int, rows: int) -> float:
    grid = np.linspace(0.0, threshold, 201)
    return float(np.trapezoid(1.0 - (1.0 - grid**rows) ** bands, grid))


@lru_cache(maxsize=64)
def optimal_bands(threshold: float, num_perm: int, min_recall: float = 0.98) -> tuple[int, int]:
    """Choose the LSH layout ``(bands, rows)`` with ``bands * rows <= num_perm``.

    Candidates are always re-verified against full signatures, so a false positive costs a
    vectorized comparison while a false negative silently keeps a duplicate. Among layouts
    whose candidate probability *at the threshold* is at least ``min_recall``, pick the one
    with the smallest false-positive area below the threshold (the S-curve analysis of
    Leskovec et al., *Mining of Massive Datasets*, ch. 3). If no layout reaches
    ``min_recall`` (very small ``num_perm``), the layout with the highest recall wins.
    """
    feasible: list[tuple[float, int, int]] = []
    fallback: tuple[float, int, int] = (-1.0, 1, 1)
    for bands in range(1, num_perm + 1):
        for rows in range(1, num_perm // bands + 1):
            recall = candidate_probability(threshold, bands, rows)
            if recall >= min_recall:
                feasible.append((_false_positive_area(threshold, bands, rows), bands, rows))
            elif recall > fallback[0]:
                fallback = (recall, bands, rows)
    if feasible:
        _, bands, rows = min(feasible)
        return bands, rows
    return fallback[1], fallback[2]


def band_hashes(signatures: UInt32Array, bands: int, rows: int) -> npt.NDArray[np.uint64]:
    """Hash each band of each signature row: shape ``(n_docs, bands)``."""
    count = signatures.shape[0]
    out = np.zeros((count, bands), dtype=np.uint64)
    with np.errstate(over="ignore"):
        for band in range(bands):
            block = signatures[:, band * rows : (band + 1) * rows].astype(np.uint64)
            combined = np.full(count, np.uint64(band + 1), dtype=np.uint64)
            for column in range(rows):
                combined = combined * _BAND_POLY + block[:, column]
            out[:, band] = combined
    return out
