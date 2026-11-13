from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataforge import __version__
from dataforge.cli.app import app
from dataforge.inspection import UnsafeQueryError, run_sql
from tests.support import REPO_ROOT

RUNNER = CliRunner()


def invoke(*args: str) -> tuple[int, str]:
    result = RUNNER.invoke(app, list(args))
    return result.exit_code, result.output


def test_version_and_help() -> None:
    code, output = invoke("--version")
    assert (code, output.strip()) == (0, f"dataforge {__version__}")
    code, output = invoke("--help")
    assert code == 0
    for command in ("doctor", "sources", "pipeline", "dataset", "contamination", "privacy", "quality", "mixture"):
        assert command in output


def test_doctor_json() -> None:
    code, output = invoke("doctor", "--json")
    checks = {c["name"]: c for c in json.loads(output)}
    assert code == 0
    assert checks["python"]["status"] == "ok"
    assert checks["duckdb"]["status"] == "ok"


def test_sources_list_and_plan() -> None:
    code, output = invoke("sources", "list", "--registry", str(REPO_ROOT / "configs" / "sources" / "demo.yaml"))
    assert code == 0
    assert "encyclopedia" in output
    code, output = invoke("pipeline", "plan", str(REPO_ROOT / "configs" / "pipelines" / "demo.yaml"))
    assert code == 0
    assert "observe:exact_dedup" in output


def test_run_rejects_unknown_until_stage() -> None:
    code, output = invoke("pipeline", "run", str(REPO_ROOT / "configs" / "pipelines" / "demo.yaml"), "--until", "nope")
    assert code == 2
    assert "unknown stage" in output


def test_sample_generate(tmp_path: Path) -> None:
    code, output = invoke("sample", "generate", str(tmp_path / "corpus"), "--scale", "0.05")
    assert code == 0
    assert (tmp_path / "corpus" / "ground_truth.jsonl").is_file()
    assert "Planted phenomena" in output


@pytest.mark.parametrize(
    "query",
    ["DELETE FROM docs", "COPY docs TO 'x.csv'", "SELECT 1; SELECT 2", "INSTALL httpfs", "ATTACH 'x.db'"],
)
def test_sql_accepts_only_single_select(tmp_path: Path, query: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"text": ["a"]}), tmp_path / "x.parquet")
    with pytest.raises(UnsafeQueryError):
        run_sql(tmp_path, query)
    columns, rows = run_sql(tmp_path, "SELECT count(*) AS n FROM docs")
    assert (columns, rows) == (["n"], [(1,)])


def test_report_without_runs(tmp_path: Path) -> None:
    code, output = invoke("report", "--work-dir", str(tmp_path))
    assert code == 0
    assert "Runs" in output
    code, output = invoke("report", "latest", "--work-dir", str(tmp_path))
    assert code == 2
    assert "no runs recorded" in output
