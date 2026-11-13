from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dataforge.config.models import NearDedupConfig, ParagraphDedupConfig
from dataforge.dedup.bloom import BloomFilter
from dataforge.dedup.exact import ExactDeduplicator, ExactDedupResult
from dataforge.dedup.minhash import (
    MinHasher,
    band_hashes,
    candidate_probability,
    jaccard_estimate,
    optimal_bands,
)
from dataforge.dedup.near import NearDeduplicator, NearDedupResult
from dataforge.dedup.paragraph import ParagraphDeduplicator
from dataforge.dedup.shingles import ngram_hashes, normalize_for_matching, shingle_hashes, tokenize, word_hashes
from dataforge.dedup.unionfind import UnionFind
from dataforge.domain.document import Document
from dataforge.synthetic.text import TextGenerator
from tests.support import make_document

GEN = TextGenerator(random.Random(11))


def article(seed: int) -> str:
    return TextGenerator(random.Random(seed)).document("science")[1]


# --------------------------------------------------------------------------- shingles


def test_tokenize_and_normalize() -> None:
    assert tokenize("Hello, World! It's 2024.") == ["hello", "world", "it", "s", "2024"]
    assert normalize_for_matching("Hello,   World!\nIt's") == "hello world it s"


def test_ngram_hashes_windows_and_short_inputs() -> None:
    hashes = word_hashes(["a", "b", "c", "d"])
    assert ngram_hashes(hashes, 2).shape == (3,)
    assert ngram_hashes(hashes, 10).shape == (1,)
    assert ngram_hashes(hashes, 10, short_as_single=False).shape == (0,)
    assert ngram_hashes(word_hashes([]), 3).shape == (0,)
    assert ngram_hashes(word_hashes(["a", "b"]), 2)[0] != ngram_hashes(word_hashes(["b", "a"]), 2)[0]


# --------------------------------------------------------------------------- exact


def test_exact_dedup_policies() -> None:
    short = make_document("same text", quality=0.2)
    duplicate = make_document("same text", quality=0.9)
    unique = make_document("other text")
    for policy, keep in (("keep_first", [True, False, True]), ("keep_highest_quality", [False, True, True])):
        dedup = ExactDeduplicator(policy)  # type: ignore[arg-type]
        dedup.observe([short, duplicate, unique])
        result, rows = dedup.finalize()
        assert result.keep.tolist() == keep
        assert result.in_group.tolist() == [True, True, False]
        assert (result.duplicates, result.groups, result.largest_group) == (1, 1, 2)
        assert rows[0]["content_hash"] == short.metadata.content_hash
        assert result.duplicate_bytes == len("same text")


def test_exact_dedup_keep_longest_ties_break_by_order() -> None:
    a = make_document("x")
    b = make_document("x")
    dedup = ExactDeduplicator("keep_longest")
    dedup.observe([a, b])
    result, _ = dedup.finalize()
    assert result.keep.tolist() == [True, False]


def test_exact_dedup_empty_and_persistence(tmp_path: Path) -> None:
    result, rows = ExactDeduplicator().finalize()
    assert result.documents == 0
    assert rows == []
    dedup = ExactDeduplicator()
    dedup.observe([make_document("a"), make_document("a"), make_document("b")])
    result, _ = dedup.finalize()
    result.save(tmp_path / "x.npz")
    loaded = ExactDedupResult.load(tmp_path / "x.npz")
    assert loaded.keep.tolist() == result.keep.tolist()
    assert loaded.summary() == result.summary()


# --------------------------------------------------------------------------- minhash / LSH


def test_minhash_estimates_jaccard() -> None:
    hasher = MinHasher(256, seed=3)
    errors = []
    for seed in range(30):
        base = article(seed)
        variant = TextGenerator(random.Random(seed + 1000)).mutate(base, edits=6)
        left, right = shingle_hashes(base, 5), shingle_hashes(variant, 5)
        exact = len(np.intersect1d(left, right)) / len(np.union1d(left, right))
        errors.append(jaccard_estimate(hasher.signature(left), hasher.signature(right)) - exact)
    assert abs(float(np.mean(errors))) < 0.03
    assert float(np.std(errors)) < 0.06


def test_batched_signatures_match_single() -> None:
    hasher = MinHasher(64, seed=1)
    batch = [shingle_hashes(article(i), 5) for i in range(12)] + [np.empty(0, dtype=np.uint64)]
    stacked = hasher.signatures(batch)
    for index, hashes in enumerate(batch):
        assert np.array_equal(stacked[index], hasher.signature(hashes))
    assert (stacked[-1] == np.iinfo(np.uint32).max).all()


def test_signature_is_deterministic_per_seed() -> None:
    hashes = shingle_hashes(article(5), 5)
    assert np.array_equal(MinHasher(64, 7).signature(hashes), MinHasher(64, 7).signature(hashes))
    assert not np.array_equal(MinHasher(64, 7).signature(hashes), MinHasher(64, 8).signature(hashes))


@pytest.mark.parametrize("threshold", [0.7, 0.8, 0.85, 0.9])
def test_optimal_bands_meets_recall_target(threshold: float) -> None:
    bands, rows = optimal_bands(threshold, 128)
    assert bands * rows <= 128
    assert candidate_probability(threshold, bands, rows) >= 0.98
    # Dissimilar pairs rarely become candidates (and candidates are verified anyway).
    assert candidate_probability(threshold - 0.4, bands, rows) < 0.1


def test_band_hashes_shape_and_identity() -> None:
    signatures = np.random.default_rng(0).integers(0, 2**32, size=(5, 32), dtype=np.uint64).astype(np.uint32)
    hashed = band_hashes(signatures, 4, 8)
    assert hashed.shape == (5, 4)
    assert np.array_equal(band_hashes(signatures[:1], 4, 8), hashed[:1])


def test_union_find_roots_are_smallest_member() -> None:
    union = UnionFind(6)
    union.union(4, 2)
    union.union(2, 5)
    union.union(1, 0)
    assert union.roots().tolist() == [0, 0, 2, 3, 2, 2]


# --------------------------------------------------------------------------- near dedup


def run_near(
    documents: list[Document], tmp_path: Path, **config: object
) -> tuple[NearDedupResult, list[dict[str, Any]]]:
    dedup = NearDeduplicator(NearDedupConfig(**config), tmp_path / "spill")  # type: ignore[arg-type]
    dedup.observe(documents)
    result, rows = dedup.finalize()
    dedup.discard()
    return result, rows


def test_near_duplicates_cluster_and_distinct_documents_do_not(tmp_path: Path) -> None:
    base = article(1)
    copy = base.replace("The ", "A ", 1) + " Updated."
    docs = [make_document(base), make_document(article(2)), make_document(copy), make_document(article(3))]
    result, rows = run_near(docs, tmp_path)
    assert result.clusters == 1
    assert result.keep.tolist().count(False) == 1
    members = {row["document_id"] for row in rows}
    assert members == {docs[0].document_id, docs[2].document_id}
    representative = next(row for row in rows if row["is_representative"])
    assert representative["document_id"] == docs[2].document_id  # keep_longest
    assert all(0.85 <= row["similarity"] <= 1.0 for row in rows)


def test_near_dedup_representative_is_order_stable(tmp_path: Path) -> None:
    base = article(4)
    docs = [make_document(base), make_document(base + " x")]
    first, _ = run_near(docs, tmp_path / "a", policy="keep_first")
    assert first.keep.tolist() == [True, False]
    cluster = first.cluster_ids[0].decode()
    second, _ = run_near(docs, tmp_path / "b", policy="keep_first")
    assert second.cluster_ids[0].decode() == cluster
    assert cluster == f"nd-{docs[0].document_id[:16]}"


def test_near_dedup_ignores_documents_without_shingles(tmp_path: Path) -> None:
    result, _ = run_near([make_document("!!!"), make_document("???")], tmp_path)
    assert result.clusters == 0
    empty, rows = run_near([], tmp_path / "empty")
    assert (empty.documents, rows) == (0, [])


def test_near_result_round_trip(tmp_path: Path) -> None:
    base = article(9)
    result, _ = run_near([make_document(base), make_document(base)], tmp_path)
    result.save(tmp_path / "n.npz")
    loaded = NearDedupResult.load(tmp_path / "n.npz")
    assert loaded.summary() == result.summary()
    assert loaded.cluster_ids.tolist() == result.cluster_ids.tolist()


# --------------------------------------------------------------------------- bloom / paragraph


def test_bloom_filter_has_no_false_negatives(tmp_path: Path) -> None:
    bloom = BloomFilter(capacity=5000, error_rate=0.01)
    keys = [f"key-{i}".encode() for i in range(5000)]
    assert not any(bloom.add(key) for key in keys[:10])
    for key in keys:
        bloom.add(key)
    assert all(key in bloom for key in keys)
    false_positives = sum(f"other-{i}".encode() in bloom for i in range(5000))
    assert false_positives < 5000 * 0.03
    bloom.save(tmp_path / "b.bin")
    restored = BloomFilter(capacity=5000, error_rate=0.01)
    restored.load(tmp_path / "b.bin")
    assert keys[0] in restored
    with pytest.raises(ValueError, match="does not match"):
        BloomFilter(capacity=10, error_rate=0.01).load(tmp_path / "b.bin")


def test_paragraph_dedup_removes_repeated_paragraphs() -> None:
    footer = "Subscribe to our newsletter for weekly updates on science and technology news."
    dedup = ParagraphDeduplicator(ParagraphDedupConfig(enabled=True, min_chars=40))
    first = dedup.process(f"{article(1)}\n\n{footer}\n\nShort line.")
    second = dedup.process(f"{article(2)}\n\n{footer}\n\nShort line.")
    assert first.removed == 0
    assert second.removed == 1
    assert footer not in second.text
    assert second.text.endswith("Short line.")
    assert dedup.process(footer).text == ""
