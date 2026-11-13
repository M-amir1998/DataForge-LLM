"""Integration: sources -> ingestion -> normalization -> dedup -> privacy -> quality -> split -> shards."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from dataforge.pipelines.runner import RunOptions, RunResult, run_pipeline
from dataforge.provenance.manifest import read_manifest
from dataforge.validation.dataset import validate_dataset

ConfigFactory = Callable[..., Path]


def shard_rows(result: RunResult, sql: str) -> list[tuple[object, ...]]:
    assert result.dataset_dir is not None
    files = (result.dataset_dir / "data").as_posix()
    return duckdb.connect().execute(sql.replace("{docs}", f"read_parquet('{files}/*.parquet')")).fetchall()


@pytest.fixture(scope="module")
def full_run(project: Path, tmp_path_factory: pytest.TempPathFactory) -> RunResult:
    from tests.support import write_pipeline

    config = write_pipeline(project, tmp_path_factory.mktemp("full"))
    return run_pipeline(config)


def test_full_run_publishes_a_valid_dataset(full_run: RunResult) -> None:
    assert full_run.status == "completed"
    assert full_run.dataset_dir is not None
    report = validate_dataset(full_run.dataset_dir)
    assert report.ok, report.problems
    manifest = read_manifest(full_run.dataset_dir)
    assert manifest.dataset_id == full_run.dataset_id
    assert manifest.totals.documents > 0
    assert {s.source_id for s in manifest.sources} >= {"web", "code", "misc"}
    assert all(len(s.fingerprint) == 64 for s in manifest.sources)
    assert manifest.code.code_fingerprint
    assert manifest.pipeline.config["pipeline"]["name"] == "smoke"
    assert [b.name for b in manifest.benchmarks] == ["demo_math", "demo_code"]
    for path in ("DATASET_CARD.md", "reports/report.md", "reports/summary.json", "reports/removals.parquet",
                 "reports/plots/stage_funnel.svg", "reports/validation.json"):  # fmt: skip
        assert (full_run.dataset_dir / path).is_file(), path


def test_every_stage_reports_consistent_counts(full_run: RunResult) -> None:
    ordered = [s for s in full_run.stats if s.name != "ingest"]
    for previous, current in itertools.pairwise(ordered):
        assert previous.documents_out == current.documents_in, (previous.name, current.name)
    for stats in ordered:
        removed = stats.documents_in - stats.documents_out
        assert removed == sum(stats.rejected.values()), stats.name


def test_governance_holds_in_the_output(full_run: RunResult) -> None:
    rows = shard_rows(
        full_run,
        "SELECT count(*) FILTER (WHERE regexp_matches(text, '[A-Za-z0-9._%+-]+@example\\.(com|org|net)')), "
        "count(*) FILTER (WHERE license IN ('UNKNOWN', 'GPL-3.0-only', 'proprietary')), "
        "count(*) FILTER (WHERE language IS NOT NULL AND language <> 'en'), "
        "count(*) - count(DISTINCT content_hash), "
        "count(*) FILTER (WHERE text LIKE '%<EMAIL>%' OR text LIKE '%<SECRET>%') "
        "FROM {docs}",
    )
    emails, bad_licenses, foreign, duplicates, redacted = (int(str(v)) for v in rows[0])
    assert (emails, bad_licenses, foreign, duplicates) == (0, 0, 0, 0)
    assert redacted > 0


def test_provenance_is_complete(full_run: RunResult) -> None:
    rows = shard_rows(
        full_run,
        "SELECT count(*) FILTER (WHERE provenance.source_uri IS NULL OR provenance.record_locator IS NULL), "
        "count(*) FILTER (WHERE len(provenance.transformations) > 0) FROM {docs}",
    )
    missing, transformed = (int(str(v)) for v in rows[0])
    assert missing == 0
    assert transformed > 0


def test_removal_audit_log_matches_stage_counts(full_run: RunResult) -> None:
    assert full_run.dataset_dir is not None
    removals = pq.read_table(full_run.dataset_dir / "reports" / "removals.parquet").to_pylist()
    rejected = sum(sum(s.rejected.values()) for s in full_run.stats)
    assert len(removals) == rejected
    assert all(r["document_id"] and r["stage"] and r["reason"] for r in removals)


def test_reports_never_contain_planted_values(full_run: RunResult) -> None:
    assert full_run.dataset_dir is not None
    reports = full_run.dataset_dir / "reports"
    blob = "".join(p.read_text(encoding="utf-8") for p in reports.glob("*.json*")) + (reports / "report.md").read_text(
        encoding="utf-8"
    )
    assert "@example.com" not in blob
    assert "555-01" not in blob
    assert "A neighbourhood bakery" not in blob  # benchmark text never copied into evidence


def test_rerun_is_immutable_and_changes_reuse_cached_segments(pipeline_config: ConfigFactory) -> None:
    config = pipeline_config(storage={"cleanup": "never"})
    first = run_pipeline(config)
    assert first.status == "completed"
    again = run_pipeline(config)
    assert again.status == "exists"
    assert again.dataset_dir == first.dataset_dir
    changed = run_pipeline(config.with_name("unused.yaml") if False else _with_seed(config, 99))
    assert changed.status == "completed"
    assert changed.dataset_id != first.dataset_id
    assert [s.cached for s in changed.segments] == [True] * (len(changed.segments) - 1) + [False]


def _with_seed(config: Path, seed: int) -> Path:
    import yaml

    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    payload["stages"]["split"]["seed"] = seed
    target = config.with_name(config.stem + f"-seed{seed}.yaml")
    target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return target


def test_partial_runs(pipeline_config: ConfigFactory) -> None:
    config = pipeline_config()
    exact = run_pipeline(config, RunOptions(until="exact_dedup"))
    assert exact.status == "partial"
    assert exact.dataset_dir is None
    assert exact.barrier_summaries["exact_dedup"]["duplicates"] > 0
    ingest = run_pipeline(config, RunOptions(ingest_only=True))
    assert [s.name for s in ingest.stats] == ["ingest"]
    assert any(k.startswith("errors|web|") for k in ingest.stats[0].counters)
    scan = run_pipeline(config, RunOptions(until="contamination", force_modes={"contamination": "annotate"}))
    contamination = next(s for s in scan.stats if s.name == "contamination")
    assert sum(contamination.rejected.values()) == 0
    assert sum(contamination.flagged.values()) > 0


def test_annotate_mode_keeps_documents_and_flags_them(pipeline_config: ConfigFactory) -> None:
    result = run_pipeline(
        pipeline_config(stages={"quality": {"mode": "annotate"}, "mixture": {"enabled": False}}),
    )
    quality = next(s for s in result.stats if s.name == "quality")
    assert sum(quality.rejected.values()) == 0
    flagged = shard_rows(result, "SELECT count(*) FROM {docs} WHERE list_contains(flags, 'quality:quality:length')")
    assert flagged[0][0] == sum(v for k, v in quality.flagged.items() if k == "quality:length")


def test_time_split_and_jsonl_output(pipeline_config: ConfigFactory) -> None:
    config = pipeline_config(
        stages={
            "split": {
                "strategy": "time",
                "ratios": {"train": 0.8, "validation": 0.1, "test": 0.1},
                "time_cutoffs": {"validation": "2023-01-01", "test": "2024-06-01"},
            }
        },
        shard={"format": "jsonl", "max_documents_per_shard": 40},
    )
    result = run_pipeline(config)
    assert result.dataset_dir is not None
    assert validate_dataset(result.dataset_dir).ok
    manifest = read_manifest(result.dataset_dir)
    assert {s.format for s in manifest.shards} == {"jsonl"}
    assert {"train", "validation", "test"} <= set(manifest.splits)
    summary = json.loads((result.dataset_dir / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["output"]["documents"] == manifest.totals.documents
