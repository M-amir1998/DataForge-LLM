from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from dataforge.config.models import ShardConfig
from dataforge.domain.document import ContaminationHit, ContentKind, Document
from dataforge.domain.hashing import sha256_file
from dataforge.errors import DatasetExistsError, StorageBudgetExceeded, StorageError
from dataforge.sharding.writer import ShardWriter
from dataforge.storage import budget
from dataforge.storage.backend import FsspecStorage, LocalStorage, storage_for
from dataforge.storage.budget import DiskGuard, estimate
from dataforge.storage.parts import PartWriter, count_documents, iter_documents, list_parts
from dataforge.storage.schema import DOCUMENT_SCHEMA, batch_to_documents, documents_to_table, schema_matches
from dataforge.validation.dataset import iter_zstd_lines
from tests.support import make_document


def rich_document(index: int) -> Document:
    doc = make_document(f"Document number {index} with some text.", kind=ContentKind.MIXED, quality=0.75)
    meta = doc.metadata
    meta.quality_signals = {"length": 40.0, "char_entropy": 4.2}
    meta.pii_counts = {"email": 2}
    meta.secret_counts = {"github_token": 1}
    meta.contamination = [ContaminationHit("demo", "q1", "ngram", 0.6)]
    meta.flags = ["quality:length"]
    meta.extra = {"thread": "t1", "n": 3}
    meta.token_estimate = 9
    meta.split = "train" if index % 3 else "test"
    meta.near_dup_cluster = "nd-x" if index == 1 else None
    doc.provenance.transformations = ["normalize[nfc]", "pii:redact[2]"]
    return doc


def test_schema_round_trip_is_lossless() -> None:
    docs = [rich_document(i) for i in range(5)]
    table = documents_to_table(docs)
    assert schema_matches(table.schema)
    assert table.schema.metadata == DOCUMENT_SCHEMA.metadata
    restored = batch_to_documents(table)
    assert restored == docs


def test_parts_commit_atomically_and_skip(tmp_path: Path) -> None:
    writer = PartWriter(tmp_path / "parts")
    writer.write([rich_document(i) for i in range(10)])
    assert list_parts(tmp_path / "parts") == []  # uncommitted parts are invisible
    info = writer.close_part()
    assert info is not None
    assert info.documents == 10
    writer.write([rich_document(i) for i in range(10, 15)])
    writer.close_part()
    parts = list_parts(tmp_path / "parts")
    assert count_documents(parts) == 15
    skipped = [d for batch in iter_documents(parts, 4, skip=12) for d in batch]
    assert [d.text for d in skipped] == [f"Document number {i} with some text." for i in (12, 13, 14)]
    writer.write([rich_document(99)])
    writer.abort()
    assert len(list_parts(tmp_path / "parts")) == 2
    assert writer.close_part() is None


@pytest.mark.parametrize("fmt", ["parquet", "jsonl"])
def test_shard_writer_rotation_naming_and_round_trip(tmp_path: Path, fmt: str) -> None:
    config = ShardConfig(format=fmt, max_documents_per_shard=4, row_group_documents=2)  # type: ignore[arg-type]
    writer = ShardWriter(tmp_path, config)
    docs = [rich_document(i) for i in range(12)]
    writer.write(docs)
    shards = writer.close()
    train = [s for s in shards if s.split == "train"]
    assert [s.path for s in train] == [
        f"data/train-0000{i}-of-00002.{'parquet' if fmt == 'parquet' else 'jsonl.zst'}" for i in range(2)
    ]
    assert sum(s.documents for s in shards) == 12
    for shard in shards:
        assert sha256_file(tmp_path / shard.path) == shard.sha256
        assert shard.documents <= 4
    if fmt == "parquet":
        rows = pq.read_table(tmp_path / train[0].path).num_rows
        assert rows == train[0].documents
    else:
        lines = [json.loads(line) for line in iter_zstd_lines(tmp_path / train[0].path) if line]
        assert lines[0]["provenance"]["transformations"] == ["normalize[nfc]", "pii:redact[2]"]


def test_shard_writer_rotates_on_compressed_size(tmp_path: Path) -> None:
    import random

    target = 60_000
    config = ShardConfig(target_shard_bytes=target, max_documents_per_shard=10_000, row_group_documents=20)
    writer = ShardWriter(tmp_path, config)
    rng = random.Random(0)
    for index in range(400):
        words = " ".join(f"w{rng.randrange(50_000)}" for _ in range(150))  # poorly compressible text
        doc = make_document(f"{index} {words}")
        doc.metadata.split = "train"
        writer.write([doc])
    shards = writer.close()
    assert len(shards) > 1
    # Rotation happens after the row group that crosses the target; the footer adds a little more.
    assert all(s.bytes < 2 * target for s in shards[:-1])


def test_local_storage_is_atomic_and_immutable(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "out")
    storage.write_bytes("a/b.txt", b"x")
    assert storage.read_bytes("a/b.txt") == b"x"
    assert storage.list("a") == ["a/b.txt"]
    with pytest.raises(StorageError, match="escapes"):
        storage.write_bytes("../evil.txt", b"x")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}", encoding="utf-8")
    storage.publish_directory(staging, "ds/v1")
    assert storage.exists("ds/v1/manifest.json")
    again = tmp_path / "staging2"
    again.mkdir()
    (again / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetExistsError):
        storage.publish_directory(again, "ds/v1")


def test_fsspec_storage_publishes_manifest_last(tmp_path: Path) -> None:
    storage = FsspecStorage("memory://dataforge-test")
    staging = tmp_path / "stage"
    (staging / "data").mkdir(parents=True)
    (staging / "data" / "train.parquet").write_bytes(b"PAR1")
    (staging / "manifest.json").write_text("{}", encoding="utf-8")
    uri = storage.publish_directory(staging, "ds/v1")
    assert uri.startswith("memory://")
    assert storage.exists("ds/v1/manifest.json")
    assert storage.read_bytes("ds/v1/data/train.parquet") == b"PAR1"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetExistsError):
        storage.publish_directory(staging, "ds/v1")
    assert isinstance(storage_for(str(tmp_path), tmp_path), LocalStorage)
    with pytest.raises(StorageError):
        FsspecStorage("memory://x")._key("../escape")


def test_disk_estimate_and_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "disk_free_bytes", lambda _path: 10 * budget.GIB)
    ok = estimate(2 * budget.GIB, 4, tmp_path, budget.GIB, keep_intermediates=False)
    assert ok.temp_bytes == 2 * budget.GIB  # at most two live materializations
    assert ok.ok
    kept = estimate(2 * budget.GIB, 4, tmp_path, budget.GIB, keep_intermediates=True)
    assert kept.temp_bytes == 4 * budget.GIB
    too_big = estimate(30 * budget.GIB, 4, tmp_path, budget.GIB, keep_intermediates=True)
    assert not too_big.ok
    DiskGuard(tmp_path, budget.GIB).check()
    monkeypatch.setattr(budget, "disk_free_bytes", lambda _path: budget.GIB // 2)
    with pytest.raises(StorageBudgetExceeded) as caught:
        DiskGuard(tmp_path, budget.GIB).check()
    assert caught.value.exit_code == 4
