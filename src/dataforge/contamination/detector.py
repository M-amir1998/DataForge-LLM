"""Benchmark contamination detection.

Methods, strongest first:

* ``exact``     - the normalized document equals a normalized benchmark field;
* ``substring`` - every n-gram of a field occurs in the document *and* the normalized field
  is a literal substring of the normalized document;
* ``ngram``     - at least ``ngram_threshold`` of a field's distinct word n-grams occur in the
  document (the containment test used for GPT-3-style decontamination);
* ``minhash``   - whole-document Jaccard similarity with a field above ``minhash_threshold``
  (catches near-copies that are reworded throughout).

Evidence is reported as character offsets in the corpus document plus a short hash of the
normalized benchmark field; benchmark text is never copied into reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from dataforge.config.models import ContaminationConfig
from dataforge.contamination.index import MinHashIndex, NgramIndex
from dataforge.contamination.registry import Probe
from dataforge.dedup.minhash import MinHasher
from dataforge.dedup.shingles import (
    ngram_hashes,
    normalize_for_matching,
    shingle_hashes,
    tokenize_with_offsets,
    word_hashes,
)
from dataforge.domain.hashing import sha256_bytes

_METHOD_RANK: Final = {"exact": 0, "substring": 1, "ngram": 2, "minhash": 3}


@dataclass(frozen=True, slots=True)
class ContaminationMatch:
    benchmark: str
    item_id: str
    field: str
    method: str
    score: float
    matched_ngrams: int
    total_ngrams: int
    spans: tuple[tuple[int, int], ...]
    evidence_hash: str


def _merge(spans: list[tuple[int, int]], limit: int) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((s, e) for s, e in merged[:limit])


class ContaminationDetector:
    def __init__(self, probes: list[Probe], config: ContaminationConfig) -> None:
        self.probes = probes
        self.config = config
        self.methods = frozenset(config.methods)
        self.exact: dict[str, list[int]] = {}
        for index, probe in enumerate(probes):
            self.exact.setdefault(probe.normalized_hash, []).append(index)
        self.ngram_index = NgramIndex([p.ngrams for p in probes])
        self.minhasher = MinHasher(config.minhash_num_perm, seed=1729)
        self.minhash_index: MinHashIndex | None = None
        if "minhash" in self.methods and probes:
            signatures = self.minhasher.signatures([shingle_hashes(p.normalized, config.shingle_size) for p in probes])
            self.minhash_index = MinHashIndex(signatures, config.minhash_threshold, self.minhasher)

    def scan(self, text: str) -> list[ContaminationMatch]:
        if not self.probes:
            return []
        cfg = self.config
        found: dict[int, ContaminationMatch] = {}
        normalized = normalize_for_matching(text)

        if "exact" in self.methods:
            for index in self.exact.get(sha256_bytes(normalized.encode("utf-8")), []):
                probe = self.probes[index]
                total = int(probe.ngrams.shape[0])
                found[index] = ContaminationMatch(
                    probe.benchmark,
                    probe.item_id,
                    probe.field,
                    "exact",
                    1.0,
                    total,
                    total,
                    ((0, len(text)),),
                    probe.evidence_hash,
                )

        if self.methods & {"substring", "ngram"}:
            words, starts, ends = tokenize_with_offsets(text)
            positional = ngram_hashes(word_hashes(words), cfg.ngram_size, short_as_single=False)
            counts = self.ngram_index.match(np.unique(positional))
            for index, matched in counts.items():
                if index in found:
                    continue
                probe = self.probes[index]
                total = int(probe.ngrams.shape[0])
                overlap = matched / total if total else 0.0
                if overlap < cfg.ngram_threshold:
                    continue
                hits = np.flatnonzero(np.isin(positional, probe.ngrams))
                spans = [(starts[i], ends[i + cfg.ngram_size - 1]) for i in hits.tolist()]
                method, score = "ngram", overlap
                if "substring" in self.methods and matched == total and probe.normalized in normalized:
                    method, score = "substring", 1.0
                elif "ngram" not in self.methods:
                    continue
                found[index] = ContaminationMatch(
                    probe.benchmark,
                    probe.item_id,
                    probe.field,
                    method,
                    round(score, 4),
                    matched,
                    total,
                    _merge(spans, cfg.max_evidence_spans),
                    probe.evidence_hash,
                )

        if self.minhash_index is not None:
            signature = self.minhasher.signature(shingle_hashes(normalized, cfg.shingle_size))
            for index, similarity in self.minhash_index.query(signature):
                if index in found:
                    continue
                probe = self.probes[index]
                total = int(probe.ngrams.shape[0])
                found[index] = ContaminationMatch(
                    probe.benchmark,
                    probe.item_id,
                    probe.field,
                    "minhash",
                    round(similarity, 4),
                    0,
                    total,
                    (),
                    probe.evidence_hash,
                )

        return sorted(found.values(), key=lambda m: (_METHOD_RANK[m.method], m.benchmark, m.item_id, m.field))
