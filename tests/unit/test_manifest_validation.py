"""Manifest schema and dataset validation failure modes on a small, real dataset."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

import pyarrow.parquet as pq
import pytest

from dataforge.config.models import ShardConfig
from dataforge.domain.hashing import sha256_file
from dataforge.errors import DatasetValidationError, InvalidManifest
from dataforge.provenance.manifest import (
    CodeEntry,
    DatasetManifest,
    PipelineEntry,
    ShardEntry,
    SplitSummary,
    read_manifest,
    write_manifest,
)
from dataforge.sharding.writer import ShardWriter
from dataforge.validation.dataset import ValidationReport, validate_dataset
from tests.support import make_document

ShardFormat = Literal["parquet", "jsonl"]


def build_dataset(root: Path, *, leak: bool = False, duplicate_ids: bool = False, fmt: ShardFormat = "parquet") -> Path:
    docs = []
    for index in range(30):
        doc = make_document(f"document {index} " * 5, record_id=f"r{index}")
        doc.metadata.split = "train" if index % 5 else "test"
        docs.append(doc)
    if leak:
        twin = make_document(docs[1].text, record_id="twin")
        twin.metadata.split = "test"
        docs.append(twin)
    if duplicate_ids:
        clone = make_document(docs[2].text, record_id="r2")
        clone.metadata.split = docs[2].metadata.split
        docs.append(clone)
    writer = ShardWriter(root, ShardConfig(format=fmt, max_documents_per_shard=10))
    writer.write(docs)
    shards = writer.close()
    splits = {
        split: SplitSummary(
            documents=sum(s.documents for s in shards if s.split == split),
            bytes=sum(s.bytes for s in shards if s.split == split),
            text_bytes=sum(s.text_bytes for s in shards if s.split == split),
            tokens=0,
            shards=sum(1 for s in shards if s.split == split),
        )
        for split in {s.split for s in shards}
    }
    manifest = DatasetManifest(
        document_schema_version="1.0",
        dataset_id="unit-abc",
        name="unit",
        version="abc",
        fingerprint="f" * 64,
        created_at="2026-01-01T00:00:00+00:00",
        run_id="run",
        code=CodeEntry(dataforge_version="0.1.0", git_sha=None, git_dirty=None, code_fingerprint="c" * 64),
        pipeline=PipelineEntry(name="unit", config_path="p.yaml", config_hash="h" * 64, stage_order=[], config={}),
        tokenizer="chars/4",
        sources=[],
        totals=SplitSummary(
            documents=sum(s.documents for s in shards), bytes=0, text_bytes=0, tokens=0, shards=len(shards)
        ),
        splits=splits,
        shards=[
            ShardEntry(
                path=s.path,
                split=s.split,
                format=fmt,
                compression=s.compression,
                documents=s.documents,
                bytes=s.bytes,
                text_bytes=s.text_bytes,
                tokens=s.tokens,
                sha256=s.sha256,
            )
            for s in shards
        ],
        stages=[],
        reproduce={"command": "x"},
    )
    write_manifest(root / "manifest.json", manifest)
    return root


def failing(report: ValidationReport) -> set[str]:
    return {c.name for c in report.checks if not c.ok}


def test_valid_dataset_passes_every_check(tmp_path: Path) -> None:
    report = validate_dataset(build_dataset(tmp_path / "ds"))
    assert report.ok, report.problems
    assert report.dataset == "ds"
    assert len(report.checks) == 9


def test_manifest_round_trip_and_errors(tmp_path: Path) -> None:
    root = build_dataset(tmp_path / "ds")
    manifest = read_manifest(root)
    write_manifest(tmp_path / "copy.json", manifest)
    assert read_manifest(tmp_path / "copy.json") == manifest
    with pytest.raises(InvalidManifest, match="not found"):
        read_manifest(tmp_path / "nope.json")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InvalidManifest, match="not valid JSON"):
        read_manifest(tmp_path / "bad.json")
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payload["schema_version"] = "9.9"
    (tmp_path / "future.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidManifest, match="unsupported"):
        read_manifest(tmp_path / "future.json")
    del payload["shards"]
    payload["schema_version"] = "1.0"
    (tmp_path / "partial.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidManifest, match="shards"):
        read_manifest(tmp_path / "partial.json")


def test_corrupted_shard_is_detected(tmp_path: Path) -> None:
    root = build_dataset(tmp_path / "ds")
    shard = next((root / "data").glob("*.parquet"))
    payload = bytearray(shard.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    shard.write_bytes(bytes(payload))
    assert "shard_sha256" in failing(validate_dataset(root))


def test_missing_shard_and_invalid_manifest(tmp_path: Path) -> None:
    root = build_dataset(tmp_path / "ds")
    next((root / "data").glob("*.parquet")).unlink()
    assert failing(validate_dataset(root)) == {"shards_exist"}
    (root / "manifest.json").write_text("[]", encoding="utf-8")
    report = validate_dataset(root)
    assert failing(report) == {"manifest"}
    with pytest.raises(DatasetValidationError):
        report.raise_if_failed()


def test_row_count_and_totals_mismatch(tmp_path: Path) -> None:
    root = build_dataset(tmp_path / "ds")
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payload["shards"][0]["documents"] += 1
    (root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    assert {"shard_rows_and_schema", "totals"} <= failing(validate_dataset(root))


def test_duplicate_ids_and_split_leakage_are_detected(tmp_path: Path) -> None:
    assert "unique_document_ids" in failing(validate_dataset(build_dataset(tmp_path / "dup", duplicate_ids=True)))
    assert "no_split_leakage" in failing(validate_dataset(build_dataset(tmp_path / "leak", leak=True)))


def test_foreign_schema_is_rejected(tmp_path: Path) -> None:
    root = build_dataset(tmp_path / "ds")
    shard = next((root / "data").glob("*.parquet"))
    table = pq.read_table(shard).drop_columns(["title"])
    pq.write_table(table, shard)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["shards"]:
        if entry["path"].endswith(shard.name):
            entry["sha256"] = sha256_file(shard)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "shard_rows_and_schema" in failing(validate_dataset(root))
    shutil.rmtree(root)


@pytest.mark.parametrize("fmt", ["parquet", "jsonl"])
def test_integrity_checks_cover_every_shard_format(tmp_path: Path, fmt: ShardFormat) -> None:
    assert validate_dataset(build_dataset(tmp_path / "ok", fmt=fmt)).ok
    assert "unique_document_ids" in failing(
        validate_dataset(build_dataset(tmp_path / "dup", duplicate_ids=True, fmt=fmt))
    )
    assert "no_split_leakage" in failing(validate_dataset(build_dataset(tmp_path / "leak", leak=True, fmt=fmt)))
