from __future__ import annotations

from pathlib import Path

import pytest

from dataforge.domain.document import ContentKind
from dataforge.domain.hashing import (
    canonical_json,
    combine_fingerprints,
    content_hash,
    fingerprint,
    hash_parts,
    hex_to_uint64_pair,
    sha256_file,
    stable_document_id,
    unit_interval,
)
from tests.support import make_document


def test_content_hash_is_128_bit_hex_and_deterministic() -> None:
    digest = content_hash("hello world")
    assert len(digest) == 32
    assert digest == content_hash("hello world")
    assert digest != content_hash("hello world!")


def test_content_hash_tolerates_lone_surrogates() -> None:
    assert len(content_hash("broken \ud800 text")) == 32


def test_document_id_depends_on_source_record_and_content() -> None:
    base = stable_document_id("web", "rec-1", "text")
    assert base == stable_document_id("web", "rec-1", "text")
    assert base != stable_document_id("books", "rec-1", "text")
    assert base != stable_document_id("web", "rec-2", "text")
    assert base != stable_document_id("web", "rec-1", "text v2")


def test_hash_parts_is_separator_safe() -> None:
    assert hash_parts("ab", "c") != hash_parts("a", "bc")


def test_unit_interval_is_uniform_ish_and_salted() -> None:
    values = [unit_interval(str(i), "salt") for i in range(5000)]
    assert all(0.0 <= v < 1.0 for v in values)
    assert 0.45 < sum(values) / len(values) < 0.55
    assert unit_interval("key", "a") != unit_interval("key", "b")


def test_hex_to_uint64_pair_round_trips() -> None:
    digest = content_hash("x")
    high, low = hex_to_uint64_pair(digest)
    assert f"{high:016x}{low:016x}" == digest
    with pytest.raises(ValueError, match="128-bit"):
        hex_to_uint64_pair("abc")


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "f.bin"
    payload = b"\x00\x01" * 1_000_000
    path.write_bytes(payload)
    assert sha256_file(path) == hashlib.sha256(payload).hexdigest()


def test_fingerprint_ignores_key_order_but_not_values() -> None:
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
    assert (
        canonical_json({"b": 1, "a": "\N{LATIN SMALL LETTER E WITH ACUTE}"})
        == '{"a":"\N{LATIN SMALL LETTER E WITH ACUTE}","b":1}'.encode()
    )


def test_combine_fingerprints_is_order_sensitive() -> None:
    assert combine_fingerprints(["a", "b"]) != combine_fingerprints(["b", "a"])


def test_replace_text_refreshes_hash_and_records_transformation() -> None:
    doc = make_document("Original text.")
    before = doc.metadata.content_hash
    doc.replace_text("Changed text.", "test:change")
    assert doc.metadata.content_hash == content_hash("Changed text.") != before
    assert doc.provenance.transformations == ["test:change"]
    doc.replace_text("Changed text.", "test:noop")
    assert doc.provenance.transformations == ["test:change"]


def test_group_key_precedence() -> None:
    doc = make_document("abc")
    assert doc.group_key() == doc.metadata.content_hash
    doc.metadata.exact_dup_group = "exact"
    assert doc.group_key() == "exact"
    doc.metadata.near_dup_cluster = "nd-1"
    assert doc.group_key() == "nd-1"


def test_document_byte_and_kind_helpers() -> None:
    doc = make_document("na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve", kind=ContentKind.CODE)
    assert doc.num_chars == 5
    assert doc.num_bytes == 6
    assert doc.is_code
