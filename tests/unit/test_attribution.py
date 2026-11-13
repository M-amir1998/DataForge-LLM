from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.reporting.attribution import removals_by_source_and_stage, source_attribution


def write_removals(path: Path) -> Path:
    rows = [
        {"document_id": "a", "source_id": "web", "record_id": "1", "stage": "exact_dedup", "reason": "x", "bytes": 10},
        {"document_id": "b", "source_id": "web", "record_id": "2", "stage": "license", "reason": "y", "bytes": 5},
        {"document_id": "c", "source_id": "web", "record_id": "3", "stage": "license", "reason": "y", "bytes": 7},
        {"document_id": "d", "source_id": "code", "record_id": "4", "stage": "quality", "reason": "z", "bytes": 3},
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def test_removals_grouped_per_source_and_stage(tmp_path: Path) -> None:
    frame = removals_by_source_and_stage(write_removals(tmp_path / "r.parquet"))
    assert frame.rows() == [("code", "quality", 1, 3), ("web", "license", 2, 12), ("web", "exact_dedup", 1, 10)]


def test_source_attribution_includes_untouched_sources(tmp_path: Path) -> None:
    frame = source_attribution(write_removals(tmp_path / "r.parquet"), {"web": 10, "code": 4, "books": 2})
    by_source = {row["source_id"]: row for row in frame.iter_rows(named=True)}
    assert by_source["web"]["removed_documents"] == 3
    assert by_source["web"]["kept_documents"] == 7
    assert by_source["web"]["top_stage"] == "license"
    assert by_source["books"]["removed_documents"] == 0
    assert by_source["books"]["top_stage"] == "-"


def test_missing_audit_log_means_nothing_removed(tmp_path: Path) -> None:
    frame = source_attribution(tmp_path / "absent.parquet", {"web": 3})
    assert frame.row(0, named=True)["kept_documents"] == 3
