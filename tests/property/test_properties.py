"""Property-based tests for the invariants the pipeline relies on."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from dataforge.config.models import NearDedupConfig, NormalizeConfig, SplitConfig
from dataforge.dedup.exact import ExactDeduplicator
from dataforge.dedup.near import NearDeduplicator
from dataforge.domain.document import ContentKind
from dataforge.domain.hashing import content_hash, fingerprint, stable_document_id
from dataforge.normalization.text import TextNormalizer
from dataforge.privacy.pii import PIIScanner
from dataforge.privacy.spans import redact
from dataforge.provenance.manifest import SplitSummary
from dataforge.splitting.splitter import Splitter
from dataforge.storage.schema import batch_to_documents, documents_to_table
from tests.support import make_document

ANY_TEXT = st.text(
    alphabet=st.characters(codec="utf-8") | st.sampled_from(list("\r\n\t \x00\u200b\u2028\u00a0")),
    max_size=300,
)
NORMALIZER = TextNormalizer(NormalizeConfig())
NFKC = TextNormalizer(NormalizeConfig(unicode_form="NFKC"))


@given(ANY_TEXT, st.sampled_from(list(ContentKind)))
def test_normalization_is_idempotent(text: str, kind: ContentKind) -> None:
    for normalizer in (NORMALIZER, NFKC):
        once = normalizer.normalize(text, kind).text
        assert normalizer.normalize(once, kind).text == once
        assert not normalizer.normalize(once, kind).changed


@given(ANY_TEXT)
def test_normalized_text_is_valid_utf8_without_controls(text: str) -> None:
    out = NORMALIZER.normalize(text + "\ud800").text
    out.encode("utf-8")
    assert not any(ord(c) < 32 and c not in "\n\t" for c in out)
    assert "\r" not in out


@given(st.text(max_size=200), st.text(max_size=20), st.text(max_size=20))
def test_hashes_are_stable_and_sensitive(text: str, source: str, record: str) -> None:
    assert content_hash(text) == content_hash(text)
    doc_id = stable_document_id(source, record, text)
    assert doc_id == stable_document_id(source, record, text)
    assert doc_id != stable_document_id(source + "x", record, text)
    assert len(doc_id) == 32


@given(st.dictionaries(st.text(max_size=8), st.integers() | st.text(max_size=8), max_size=8))
def test_config_fingerprint_is_order_independent(mapping: dict[str, object]) -> None:
    reversed_mapping = dict(reversed(list(mapping.items())))
    assert fingerprint(mapping) == fingerprint(reversed_mapping)


@given(
    st.lists(st.text(min_size=1, max_size=40), min_size=1, max_size=40),
    st.integers(min_value=0, max_value=10_000),
)
def test_split_is_deterministic_and_group_isolated(texts: list[str], seed: int) -> None:
    config = SplitConfig(ratios={"train": 0.6, "validation": 0.2, "test": 0.2}, seed=seed)
    docs = [make_document(t, record_id=f"r{i}") for i, t in enumerate(texts)]
    first = [Splitter(config).assign(d) for d in docs]
    second = [Splitter(config).assign(d) for d in docs]
    assert first == second
    by_text: dict[str, set[str]] = {}
    for doc, split in zip(docs, first, strict=True):
        by_text.setdefault(doc.text, set()).add(split)
    assert all(len(splits) == 1 for splits in by_text.values())


@given(st.lists(st.sampled_from(["alpha", "beta", "gamma", "delta", "x" * 50]), min_size=0, max_size=60))
def test_exact_dedup_keeps_exactly_one_per_content(texts: list[str]) -> None:
    docs = [make_document(t, record_id=str(i)) for i, t in enumerate(texts)]
    dedup = ExactDeduplicator("keep_longest")
    dedup.observe(docs)
    result, rows = dedup.finalize()
    kept = [d.text for d, keep in zip(docs, result.keep, strict=True) if keep]
    assert sorted(kept) == sorted(set(texts))
    assert result.duplicates == len(texts) - len(set(texts)) == len(rows)


@given(st.lists(st.integers(min_value=0, max_value=4), min_size=2, max_size=25))
def test_near_dedup_keeps_one_representative_per_cluster(choices: list[int]) -> None:
    bases = [
        " ".join(f"token{b}_{i}" for i in range(60)) for b in range(5)
    ]  # five unrelated documents; repeats are exact (Jaccard 1) duplicates
    docs = [make_document(bases[c], record_id=str(i)) for i, c in enumerate(choices)]
    with tempfile.TemporaryDirectory() as tmp:
        dedup = NearDeduplicator(NearDedupConfig(), Path(tmp) / "spill")
        dedup.observe(docs)
        result, _ = dedup.finalize()
        dedup.discard()
    kept = [c for c, keep in zip(choices, result.keep, strict=True) if keep]
    assert sorted(kept) == sorted(set(choices))


@given(
    st.lists(st.sampled_from(["jane.doe@example.com", "+1 202-555-0143", "203.0.113.9", "plain words"]), max_size=12)
)
def test_redaction_removes_every_detected_value(parts: list[str]) -> None:
    scanner = PIIScanner()
    text = " and ".join(parts)
    redacted = redact(text, scanner.scan(text))
    assert scanner.scan(redacted) == []
    for value in ("jane.doe@example.com", "202-555-0143", "203.0.113.9"):
        assert value not in redacted


@given(
    st.lists(
        st.tuples(
            st.text(max_size=60),
            st.floats(min_value=0, max_value=1, allow_nan=False),
            st.sampled_from(list(ContentKind)),
        ),
        min_size=1,
        max_size=10,
    )
)
def test_schema_round_trip(rows: list[tuple[str, float, ContentKind]]) -> None:
    docs = []
    for index, (text, score, kind) in enumerate(rows):
        doc = make_document(text, record_id=str(index), kind=kind, quality=score)
        doc.metadata.quality_signals = {"length": float(len(text))}
        docs.append(doc)
    assert batch_to_documents(documents_to_table(docs)) == docs


@given(st.integers(min_value=0, max_value=10**12), st.integers(min_value=0, max_value=1000))
def test_manifest_models_round_trip(documents: int, shards: int) -> None:
    summary = SplitSummary(
        documents=documents, bytes=documents * 3, text_bytes=documents, tokens=documents // 4, shards=shards
    )
    assert SplitSummary.model_validate_json(summary.model_dump_json()) == summary
