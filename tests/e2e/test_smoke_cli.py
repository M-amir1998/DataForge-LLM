"""End to end: the real CLI, as Jenkins runs it, on tiny generated data (no network, no GPU)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dataforge.pipelines.runner import run_pipeline
from dataforge.telemetry.metrics import MemorySampler
from tests.support import REPO_ROOT, build_project

pytestmark = pytest.mark.slow


def dataforge(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, "-m", "dataforge", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )


@pytest.fixture(scope="module")
def smoke_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("e2e")
    shutil.copytree(REPO_ROOT / "configs", root / "configs")
    shutil.copytree(REPO_ROOT / "benchmarks" / "contamination", root / "benchmarks" / "contamination")
    return root


def test_smoke_pipeline_through_the_cli(smoke_project: Path) -> None:
    generated = dataforge(smoke_project, "sample", "generate", "data/smoke", "--scale", "0.1")
    assert generated.returncode == 0, generated.stderr
    run = dataforge(smoke_project, "pipeline", "run", "configs/pipelines/smoke.yaml", "--quiet")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "status: completed" in run.stdout

    datasets = sorted((smoke_project / ".dataforge" / "smoke" / "datasets" / "smoke").iterdir())
    assert len(datasets) == 1
    dataset = datasets[0]
    for required in ("manifest.json", "DATASET_CARD.md", "reports/report.md", "reports/summary.json"):
        assert (dataset / required).is_file(), required
    assert any((dataset / "data").glob("train-*.parquet"))
    assert not (smoke_project / ".dataforge" / "smoke" / "work" / "segments").exists() or not any(
        (smoke_project / ".dataforge" / "smoke" / "work" / "segments").iterdir()
    ), "cleanup: on_success removes intermediates"

    validate = dataforge(smoke_project, "dataset", "validate", str(dataset), "--json")
    assert validate.returncode == 0, validate.stdout
    assert json.loads(validate.stdout)["ok"] is True

    inspect = dataforge(smoke_project, "dataset", "inspect", str(dataset), "--json")
    summary = json.loads(inspect.stdout)
    assert summary["documents"] > 0
    assert summary["estimated_tokens"] > 0

    sql = dataforge(
        smoke_project, "dataset", "sql", str(dataset), "SELECT split, count(*) AS n FROM docs GROUP BY split"
    )
    assert sql.returncode == 0
    assert "train" in sql.stdout

    report = dataforge(smoke_project, "report", "latest", "--work-dir", ".dataforge/smoke/work")
    assert report.returncode == 0
    assert "status=completed" in report.stdout

    rerun = dataforge(smoke_project, "pipeline", "run", "configs/pipelines/smoke.yaml", "--quiet")
    assert "status: exists" in rerun.stdout

    privacy = dataforge(smoke_project, "privacy", "scan", str(dataset), "--json")
    scan = json.loads(privacy.stdout)
    assert scan["detections"].get("email", 0) == 0  # emails were redacted upstream


def test_fixture_pipeline_stays_within_memory_bounds(tmp_path: Path) -> None:
    project = build_project(tmp_path / "project")
    from tests.support import write_pipeline

    sampler = MemorySampler()
    sampler.sample()
    result = run_pipeline(write_pipeline(project, tmp_path / "storage"))
    sampler.sample()
    assert result.status == "completed"
    # Generous bound: the smoke run peaks well under 1 GiB; exact OS accounting varies by platform.
    assert result.peak_rss_bytes < 1.5 * 2**30
