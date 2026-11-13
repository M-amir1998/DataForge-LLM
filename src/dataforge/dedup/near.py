"""Near-duplicate detection with MinHash + LSH and union-find clustering.

Bounded memory: signatures are appended to a spill file during observation and memory-mapped
for finalization; only small per-document arrays stay in RAM. Candidate pairs come from LSH
buckets (each member is compared with its bucket anchor), are verified against the Jaccard
threshold using full signatures, and are merged with union-find. Similarities reported for
cluster members are estimated against the cluster representative and can fall below the
threshold for transitively linked members.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import numpy as np
import numpy.typing as npt

from dataforge.config.models import NearDedupConfig
from dataforge.dedup.minhash import MinHasher, band_hashes, optimal_bands
from dataforge.dedup.policy import representative_keys
from dataforge.dedup.shingles import shingle_hashes
from dataforge.dedup.unionfind import UnionFind
from dataforge.domain.document import Document

_PAIR_CHUNK = 65_536
_BAND_CHUNK = 262_144


@dataclass(frozen=True, slots=True)
class NearDedupResult:
    keep: npt.NDArray[np.bool_]
    representative: npt.NDArray[np.int64]  # -1 when the document is in no cluster
    similarity: npt.NDArray[np.float32]
    cluster_ids: npt.NDArray[np.bytes_]  # b"" when the document is in no cluster
    documents: int
    duplicates: int
    duplicate_bytes: int
    clusters: int
    largest_cluster: int
    candidate_pairs: int
    verified_pairs: int
    bands: int
    rows: int

    def save(self, path: Path) -> None:
        stats = np.array(
            [
                self.documents,
                self.duplicates,
                self.duplicate_bytes,
                self.clusters,
                self.largest_cluster,
                self.candidate_pairs,
                self.verified_pairs,
                self.bands,
                self.rows,
            ],
            dtype=np.int64,
        )
        np.savez(
            path,
            keep=self.keep,
            representative=self.representative,
            similarity=self.similarity,
            cluster_ids=self.cluster_ids,
            stats=stats,
        )

    @classmethod
    def load(cls, path: Path) -> NearDedupResult:
        with np.load(path, allow_pickle=False) as data:
            stats = [int(v) for v in data["stats"]]
            return cls(
                data["keep"].copy(),
                data["representative"].copy(),
                data["similarity"].copy(),
                data["cluster_ids"].copy(),
                *stats,
            )

    def summary(self) -> dict[str, int]:
        return {
            "documents_before": self.documents,
            "documents_after": self.documents - self.duplicates,
            "duplicates": self.duplicates,
            "duplicate_bytes": self.duplicate_bytes,
            "clusters": self.clusters,
            "largest_cluster": self.largest_cluster,
            "candidate_pairs": self.candidate_pairs,
            "verified_pairs": self.verified_pairs,
            "bands": self.bands,
            "rows_per_band": self.rows,
        }


class NearDeduplicator:
    def __init__(self, config: NearDedupConfig, spill_dir: Path) -> None:
        self.config = config
        self.minhasher = MinHasher(config.num_perm, config.seed)
        if config.bands is not None and config.rows is not None:
            self.bands, self.rows = config.bands, config.rows
        else:
            self.bands, self.rows = optimal_bands(config.threshold, config.num_perm)
        self.spill_dir = spill_dir
        self._signature_path = spill_dir / "near_dedup.signatures.u32"
        self._handle: IO[bytes] | None = None
        self.reset()

    def reset(self) -> None:
        self.close()
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self._signature_path.open("wb")
        self._lengths: list[npt.NDArray[np.int64]] = []
        self._quality: list[npt.NDArray[np.float32]] = []
        self._ids: list[npt.NDArray[np.bytes_]] = []
        self._valid: list[npt.NDArray[np.bool_]] = []
        self._count = 0

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def discard(self) -> None:
        self.close()
        self._signature_path.unlink(missing_ok=True)

    def observe(self, documents: Sequence[Document]) -> None:
        if not documents:
            return
        if self._handle is None:
            raise RuntimeError("observe() called after finalize(); call reset() first")
        shingles = [shingle_hashes(doc.text, self.config.shingle_size) for doc in documents]
        signatures = self.minhasher.signatures(shingles)
        self._handle.write(np.ascontiguousarray(signatures).tobytes())
        self._valid.append(np.array([s.shape[0] > 0 for s in shingles], dtype=bool))
        self._lengths.append(np.array([d.num_bytes for d in documents], dtype=np.int64))
        self._quality.append(
            np.array(
                [d.metadata.quality_score if d.metadata.quality_score is not None else np.nan for d in documents],
                dtype=np.float32,
            )
        )
        self._ids.append(np.array([d.document_id for d in documents], dtype="S32"))
        self._count += len(documents)

    def _candidate_pairs(
        self, signatures: npt.NDArray[np.uint32], valid: npt.NDArray[np.bool_]
    ) -> npt.NDArray[np.int64]:
        """Unique (anchor, member) pairs sharing at least one LSH bucket."""
        valid_index = np.flatnonzero(valid)
        if valid_index.shape[0] < 2:
            return np.empty((0, 2), dtype=np.int64)
        collected: list[npt.NDArray[np.int64]] = []
        cap = self.config.max_bucket_size
        for band in range(self.bands):
            hashes = np.empty(valid_index.shape[0], dtype=np.uint64)
            for start in range(0, valid_index.shape[0], _BAND_CHUNK):
                rows = valid_index[start : start + _BAND_CHUNK]
                block = signatures[rows][:, band * self.rows : (band + 1) * self.rows]
                hashes[start : start + rows.shape[0]] = band_hashes(block, 1, self.rows)[:, 0]
            order = np.argsort(hashes, kind="stable")
            sorted_hashes = hashes[order]
            starts = np.ones(order.shape[0], dtype=bool)
            starts[1:] = sorted_hashes[1:] != sorted_hashes[:-1]
            group = np.cumsum(starts) - 1
            group_start = np.flatnonzero(starts)[group]
            rank = np.arange(order.shape[0]) - group_start
            members = (rank > 0) & (rank < cap)
            if not members.any():
                continue
            anchors = valid_index[order[group_start[members]]]
            others = valid_index[order[members]]
            collected.append(np.stack([anchors, others], axis=1))
        if not collected:
            return np.empty((0, 2), dtype=np.int64)
        pairs = np.concatenate(collected)
        return np.unique(pairs, axis=0)

    def finalize(self) -> tuple[NearDedupResult, list[dict[str, Any]]]:
        self.close()
        count = self._count
        lengths = np.concatenate(self._lengths) if self._lengths else np.empty(0, dtype=np.int64)
        quality = np.concatenate(self._quality) if self._quality else np.empty(0, dtype=np.float32)
        ids = np.concatenate(self._ids) if self._ids else np.empty(0, dtype="S32")
        valid = np.concatenate(self._valid) if self._valid else np.empty(0, dtype=bool)
        keep = np.ones(count, dtype=bool)
        representative = np.full(count, -1, dtype=np.int64)
        similarity = np.zeros(count, dtype=np.float32)
        cluster_ids = np.full(count, b"", dtype="S19")
        if count == 0:
            empty = NearDedupResult(
                keep,
                representative,
                similarity,
                cluster_ids,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                self.bands,
                self.rows,
            )
            return empty, []

        signatures = np.memmap(self._signature_path, dtype=np.uint32, mode="r", shape=(count, self.config.num_perm))
        try:
            pairs = self._candidate_pairs(signatures, valid)
            union = UnionFind(count)
            verified = 0
            for start in range(0, pairs.shape[0], _PAIR_CHUNK):
                chunk = pairs[start : start + _PAIR_CHUNK]
                sims = (signatures[chunk[:, 0]] == signatures[chunk[:, 1]]).mean(axis=1)
                for left, right in chunk[sims >= self.config.threshold].tolist():
                    union.union(left, right)
                    verified += 1
            roots = union.roots()
            sizes = np.bincount(roots, minlength=count)
            clustered = sizes[roots] > 1
            rows: list[dict[str, Any]] = []
            if clustered.any():
                ordinals = np.arange(count, dtype=np.int64)
                keys = representative_keys(self.config.policy, ordinals, lengths, quality)
                order = np.lexsort((*keys, roots))
                sorted_roots = roots[order]
                first = np.ones(count, dtype=bool)
                first[1:] = sorted_roots[1:] != sorted_roots[:-1]
                reps_sorted = order[np.flatnonzero(first)[np.cumsum(first) - 1]]
                rep_of = np.empty(count, dtype=np.int64)
                rep_of[order] = reps_sorted
                members = np.flatnonzero(clustered)
                representative[members] = rep_of[members]
                keep[members] = rep_of[members] == members
                for start in range(0, members.shape[0], _PAIR_CHUNK):
                    chunk = members[start : start + _PAIR_CHUNK]
                    similarity[chunk] = (signatures[chunk] == signatures[rep_of[chunk]]).mean(axis=1)
                for index in members.tolist():
                    rep = int(rep_of[index])
                    cluster = b"nd-" + ids[rep][:16]
                    cluster_ids[index] = cluster
                    rows.append(
                        {
                            "cluster_id": cluster.decode("ascii"),
                            "document_id": ids[index].decode("ascii"),
                            "representative_id": ids[rep].decode("ascii"),
                            "is_representative": rep == index,
                            "similarity": round(float(similarity[index]), 4),
                            "bytes": int(lengths[index]),
                        }
                    )
            removed = ~keep
            cluster_sizes = sizes[sizes > 1]
            result = NearDedupResult(
                keep=keep,
                representative=representative,
                similarity=similarity,
                cluster_ids=cluster_ids,
                documents=count,
                duplicates=int(removed.sum()),
                duplicate_bytes=int(lengths[removed].sum()),
                clusters=int(cluster_sizes.shape[0]),
                largest_cluster=int(np.max(cluster_sizes)) if cluster_sizes.shape[0] else 0,
                candidate_pairs=int(pairs.shape[0]),
                verified_pairs=verified,
                bands=self.bands,
                rows=self.rows,
            )
            return result, rows
        finally:
            del signatures  # release the mapping before the spill file is deleted (Windows)
