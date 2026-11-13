"""Every CLI command, in-process, against a real pipeline config and dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataforge.cli.app import app
from dataforge.pipelines.runner import RunResult, run_pipeline
from tests.support import write_pipeline

RUNNER = CliRunner()


@pytest.fixture(scope="module")
def produced(project: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, RunResult]:
    storage = tmp_path_factory.mktemp("cli-storage")
    config = write_pipeline(project, storage, {"storage": {"cleanup": "never"}})
    return config, run_pipeline(config)


def invoke(*args: str) -> tuple[int, str]:
    result = RUNNER.invoke(app, list(args))
    return result.exit_code, result.output


def test_pipeline_commands(produced: tuple[Path, RunResult]) -> None:
    config, _ = produced
    code, output = invoke("pipeline", "run", str(config), "--dry-run")
    assert code == 0
    assert "Disk estimate" in output
    assert "Expected output" in output
    code, output = invoke("pipeline", "run", str(config), "--quiet")
    assert (code, "status: exists" in output) == (0, True)


def test_partial_run_commands(produced: tuple[Path, RunResult]) -> None:
    config, _ = produced
    code, output = invoke("ingest", str(config), "--quiet")
    assert code == 0
    assert "Input statistics" in output
    for args, marker in (
        (("dedup", "exact"), "exact_dedup summary"),
        (("dedup", "near"), "near_dedup summary"),
        (("contamination", "scan"), "Contamination"),
        (("mixture", "build"), "Mixture"),
    ):
        code, output = invoke(*args, str(config), "--quiet")
        assert code == 0, output
        assert marker in output


def test_dataset_commands(produced: tuple[Path, RunResult]) -> None:
    _, result = produced
    assert result.dataset_dir is not None
    dataset = str(result.dataset_dir)
    code, output = invoke("dataset", "validate", dataset)
    assert code == 0
    assert "no_split_leakage" in output
    code, output = invoke("dataset", "validate", str(result.dataset_dir / "manifest.json"), "--no-hashes", "--json")
    assert json.loads(output)["ok"]
    code, output = invoke("dataset", "inspect", dataset)
    assert code == 0
    assert "Duplicate statistics" in output
    code, output = invoke(
        "dataset", "sql", dataset, "SELECT domain, count(*) AS n FROM docs GROUP BY domain ORDER BY n DESC"
    )
    assert code == 0
    assert "domain" in output
    code, output = invoke("dataset", "sql", dataset, "DROP TABLE docs")
    assert code == 2


def test_scan_commands(produced: tuple[Path, RunResult]) -> None:
    config, result = produced
    assert result.dataset_dir is not None
    code, output = invoke("quality", "analyze", str(result.dataset_dir), "--config", str(config))
    assert code == 0
    assert "would pass the configured policy" in output
    code, output = invoke("quality", "analyze", str(result.dataset_dir), "--json")
    expected = json.loads((result.dataset_dir / "manifest.json").read_text(encoding="utf-8"))["totals"]["documents"]
    assert json.loads(output)["documents"] == expected
    code, output = invoke("privacy", "scan", str(result.dataset_dir))
    assert code == 0
    assert "documents with PII" in output


def test_sources_and_report_commands(produced: tuple[Path, RunResult], project: Path) -> None:
    config, result = produced
    registry = project / "configs" / "sources" / "smoke.yaml"
    code, output = invoke("sources", "inspect", "forum", "--registry", str(registry))
    assert code == 0
    assert "PII and credentials redacted" in output
    assert "@example" not in output
    work_dir = result.run_dir.parents[1] if result.run_dir else Path()
    code, output = invoke("report", "--list", "--work-dir", str(work_dir))
    assert code == 0
    assert result.run_id is not None
    assert result.run_id in output
    code, output = invoke("report", str(result.run_id), "--work-dir", str(work_dir))
    assert code == 0
    assert "full report" in output
    code, _ = invoke("report", "missing-run", "--work-dir", str(work_dir))
    assert code == 2
    code, output = invoke("cache", "clean", "--work-dir", str(work_dir), "--yes")
    assert code == 0
    assert "removed" in output
    assert not (work_dir / "segments").exists()
    assert config.is_file()
