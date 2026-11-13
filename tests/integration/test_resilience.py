"""Failure injection, resume, disk exhaustion, and reproducibility."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataforge.cli.app import app
from dataforge.domain.document import Document
from dataforge.errors import (
    ConfigError,
    ContaminationDetected,
    DecodingError,
    PipelineStageFailure,
    SourceFormatError,
    SourceNotFound,
    StorageBudgetExceeded,
    UnsafeLicense,
)
from dataforge.pipelines.runner import RunOptions, run_pipeline
from dataforge.pipelines.stage import Emitter, StageResult
from dataforge.pipelines.stages.scoring import QualityStage
from dataforge.provenance.manifest import read_manifest
from dataforge.storage import budget

ConfigFactory = Callable[..., Path]


def shard_digests(dataset_dir: Path) -> dict[str, str]:
    return {s.path: s.sha256 for s in read_manifest(dataset_dir).shards}


def test_independent_runs_are_bit_identical(pipeline_config: ConfigFactory, tmp_path: Path) -> None:
    config = pipeline_config()
    first = run_pipeline(config)
    second = run_pipeline(config, RunOptions(use_cache=False, work_dir=tmp_path / "w2", output_dir=tmp_path / "o2"))
    assert first.dataset_dir is not None
    assert second.dataset_dir is not None
    assert first.dataset_id == second.dataset_id
    assert shard_digests(first.dataset_dir) == shard_digests(second.dataset_dir)


def test_interrupted_stage_resumes_to_identical_output(
    pipeline_config: ConfigFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Small batches and parts so the crash lands after several committed checkpoints.
    config = pipeline_config(execution={"batch_size": 16, "part_max_documents": 20})
    reference = run_pipeline(config, RunOptions(work_dir=tmp_path / "ref-w", output_dir=tmp_path / "ref-o"))

    original = QualityStage.process_batch
    calls = {"n": 0}

    def crashing(self: QualityStage, documents: Sequence[Document], emit: Emitter) -> list[StageResult]:
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("simulated crash (power loss)")
        return original(self, documents, emit)

    monkeypatch.setattr(QualityStage, "process_batch", crashing)
    with pytest.raises(PipelineStageFailure, match="quality"):
        run_pipeline(config)
    monkeypatch.setattr(QualityStage, "process_batch", original)

    resumed = run_pipeline(config)
    assert resumed.status == "completed"
    assert any(s.resumed_checkpoints > 0 for s in resumed.segments)
    assert any(s.cached for s in resumed.segments)
    assert resumed.dataset_dir is not None
    assert reference.dataset_dir is not None
    assert shard_digests(resumed.dataset_dir) == shard_digests(reference.dataset_dir)


def test_disk_full_stops_at_a_checkpoint_and_resumes(
    pipeline_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = pipeline_config()
    real = budget.disk_free_bytes
    state = {"calls": 0}

    def shrinking(path: Path) -> int:
        state["calls"] += 1
        return real(path) if state["calls"] < 6 else 0

    monkeypatch.setattr(budget, "disk_free_bytes", shrinking)
    with pytest.raises(StorageBudgetExceeded):
        run_pipeline(config)
    monkeypatch.setattr(budget, "disk_free_bytes", real)
    resumed = run_pipeline(config)
    assert resumed.status == "completed"


def test_upfront_disk_budget_refuses_oversized_runs(
    pipeline_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(budget, "disk_free_bytes", lambda _path: 1024)
    with pytest.raises(StorageBudgetExceeded, match="exceeds free space"):
        run_pipeline(pipeline_config())
    planned = run_pipeline(pipeline_config(), RunOptions(dry_run=True))
    assert planned.status == "planned"
    assert not planned.disk.ok


def _corrupt_source(project: Path, name: str, payload: bytes) -> str:
    path = project / "data" / name
    path.write_bytes(payload)
    return f"data/{name}"


def test_missing_source(pipeline_config: ConfigFactory) -> None:
    config = pipeline_config(
        sources={"registry": None, "inline": {"gone": {"type": "local", "path": "data/nope.jsonl"}}}
    )
    with pytest.raises(SourceNotFound):
        run_pipeline(config)


def test_corrupted_source_beyond_tolerance(pipeline_config: ConfigFactory, project: Path) -> None:
    relative = _corrupt_source(project, "corrupt.jsonl", b"{broken\n" * 10 + b'{"text": "ok"}\n')
    inline = {"bad": {"type": "local", "path": relative, "license": "MIT", "max_record_errors": 3}}
    with pytest.raises(SourceFormatError, match="more than 3 errors"):
        run_pipeline(pipeline_config(sources={"registry": None, "inline": inline}))


def test_invalid_utf8_policies(pipeline_config: ConfigFactory, project: Path) -> None:
    relative = _corrupt_source(
        project, "latin1.jsonl", b'{"text": "Caf\xe9 au lait is served daily in the town square."}\n'
    )
    inline = {"enc": {"type": "local", "path": relative, "license": "MIT", "on_decode_error": "fail"}}
    with pytest.raises(DecodingError):
        run_pipeline(pipeline_config(sources={"registry": None, "inline": inline}), RunOptions(ingest_only=True))
    inline["enc"]["on_decode_error"] = "replace"
    result = run_pipeline(pipeline_config(sources={"registry": None, "inline": inline}), RunOptions(ingest_only=True))
    assert result.stats[0].documents_out == 1


def test_bad_configuration_is_reported(pipeline_config: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="mixture weights must sum"):
        run_pipeline(pipeline_config(stages={"mixture": {"weights": {"general": 0.3}}}))


def test_fail_policies_raise_typed_errors(pipeline_config: ConfigFactory) -> None:
    with pytest.raises(ContaminationDetected):
        run_pipeline(pipeline_config(stages={"contamination": {"policy": "fail"}}))
    with pytest.raises(UnsafeLicense):
        run_pipeline(pipeline_config(stages={"license": {"unknown_policy": "fail"}}))


def test_cli_reproduce_is_bit_identical(
    pipeline_config: ConfigFactory, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = pipeline_config()
    result = run_pipeline(config)
    assert result.dataset_dir is not None
    monkeypatch.chdir(project)
    outcome = CliRunner().invoke(app, ["pipeline", "reproduce", str(result.dataset_dir / "manifest.json")])
    assert outcome.exit_code == 0, outcome.output
    assert "bit-identical" in outcome.output


def test_cli_exit_codes_for_failures(pipeline_config: ConfigFactory, tmp_path: Path) -> None:
    runner = CliRunner()
    bad = tmp_path / "bad.yaml"
    bad.write_text("pipeline: {name: x}\nsources: {}\n", encoding="utf-8")
    assert runner.invoke(app, ["pipeline", "plan", str(bad)]).exit_code == 2
    dataset = tmp_path / "ds"
    dataset.mkdir()
    (dataset / "manifest.json").write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    assert runner.invoke(app, ["dataset", "validate", str(dataset)]).exit_code == 5


def test_reproduce_refuses_changed_sources(
    pipeline_config: ConfigFactory, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b'{"text": "The original sentence about rivers and lakes is long enough for every filter."}\n'
    relative = _corrupt_source(project, "mutable.jsonl", original)
    inline = {"mutable": {"type": "local", "path": relative, "license": "MIT"}}
    config = pipeline_config(sources={"registry": None, "inline": inline}, stages={"mixture": {"enabled": False}})
    result = run_pipeline(config)
    assert result.dataset_dir is not None
    (project / relative).write_bytes(original.replace(b"original", b"edited"))
    monkeypatch.chdir(project)
    outcome = CliRunner().invoke(app, ["pipeline", "reproduce", str(result.dataset_dir / "manifest.json")])
    assert outcome.exit_code == 6
    assert "source content changed" in outcome.output
