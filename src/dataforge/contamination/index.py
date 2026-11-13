"""Indexes over benchmark probes: an n-gram containment index and a MinHash LSH index."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from dataforge.dedup.minhash import MinHasher, UInt32Array, band_hashes, optimal_bands


class NgramIndex:
    """Sorted (n-gram hash -> probe) postings; queries are fully vectorized.

    ``match`` returns, for every probe sharing at least one n-gram with the document, the
    number of that probe's distinct n-grams that occur in the document.
    """

    def __init__(self, probe_ngrams: Sequence[npt.NDArray[np.uint64]]) -> None:
        lengths = np.fromiter((g.shape[0] for g in probe_ngrams), dtype=np.int64, count=len(probe_ngrams))
        self.totals = lengths
        if lengths.sum() == 0:
            self.hashes = np.empty(0, dtype=np.uint64)
            self.owners = np.empty(0, dtype=np.int64)
            self.unique = self.hashes
            return
        hashes = np.concatenate([g for g in probe_ngrams if g.shape[0]])
        owners = np.repeat(np.arange(len(probe_ngrams), dtype=np.int64), lengths)
        order = np.argsort(hashes, kind="stable")
        self.hashes = hashes[order]
        self.owners = owners[order]
        self.unique = np.unique(self.hashes)

    def match(self, document_ngrams: npt.NDArray[np.uint64]) -> dict[int, int]:
        if self.unique.shape[0] == 0 or document_ngrams.shape[0] == 0:
            return {}
        present = document_ngrams[np.isin(document_ngrams, self.unique)]
        if present.shape[0] == 0:
            return {}
        left = np.searchsorted(self.hashes, present, side="left")
        right = np.searchsorted(self.hashes, present, side="right")
        counts = right - left
        total = int(counts.sum())
        starts = np.repeat(left, counts)
        offsets = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
        owners = self.owners[starts + offsets]
        tally = np.bincount(owners)
        hit = np.flatnonzero(tally)
        return {int(p): int(tally[p]) for p in hit}


class MinHashIndex:
    """LSH buckets over probe signatures for whole-document near-copy detection."""

    def __init__(self, signatures: UInt32Array, threshold: float, minhasher: MinHasher) -> None:
        self.signatures = signatures
        self.threshold = threshold
        self.minhasher = minhasher
        self.bands, self.rows = optimal_bands(threshold, minhasher.num_perm)
        self.buckets: list[dict[int, list[int]]] = [{} for _ in range(self.bands)]
        if signatures.shape[0]:
            hashed = band_hashes(signatures, self.bands, self.rows)
            for probe, row in enumerate(hashed.tolist()):
                for band, value in enumerate(row):
                    self.buckets[band].setdefault(value, []).append(probe)

    def query(self, signature: UInt32Array) -> list[tuple[int, float]]:
        if not self.buckets or self.signatures.shape[0] == 0:
            return []
        hashed = band_hashes(signature[None, :], self.bands, self.rows)[0].tolist()
        candidates = sorted({p for band, value in enumerate(hashed) for p in self.buckets[band].get(value, [])})
        if not candidates:
            return []
        sims = (self.signatures[candidates] == signature[None, :]).mean(axis=1)
        return [(probe, float(sim)) for probe, sim in zip(candidates, sims, strict=True) if sim >= self.threshold]
