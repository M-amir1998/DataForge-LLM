"""Pipeline application service: plan, execute, shard, report, validate, publish.

Dataset versions are content-addressed. The version is a fingerprint of the
output-affecting configuration, the code fingerprint, every source's content fingerprint,
and every stage's external resources (benchmark files, tokenizers). Re-running an unchanged
pipeline therefore resolves to the existing immutable dataset instead of overwriting it,
and any change yields a new version.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dataforge.config.loader import LoadedPipelineConfig, load_pipeline_config
from dataforge.config.models import StageMode, StageName
from dataforge.domain.document import DOCUMENT_SCHEMA_VERSION
from dataforge.domain.hashing import combine_fingerprints, fingerprint
from dataforge.errors import PipelineError, StorageBudgetExceeded
from dataforge.ingestion.ingest import Ingestor
from dataforge.pipelines.executor import INGEST, SegmentExecutor, SegmentOutcome
from dataforge.pipelines.graph import PipelinePlan, build_plan
from dataforge.pipelines.stage import Stage, StageContext, StageStats
from dataforge.pipelines.stages import build_stage
from dataforge.pipelines.stages.scoring import ContaminationStage
from dataforge.provenance.lineage import code_identity
from dataforge.provenance.manifest import (
    BenchmarkEntry,
    CodeEntry,
    DatasetManifest,
    FileEntry,
    PipelineEntry,
    ShardEntry,
    SourceEntry,
    SplitSummary,
    StageSummary,
    write_manifest,
)
from dataforge.reporting.report import ReportContext, write_reports
from dataforge.sharding.writer import ShardInfo, ShardWriter
from dataforge.sources.base import SourceDescription, SourceReader
from dataforge.sources.registry import SourceRegistry
from dataforge.storage.backend import MANIFEST_NAME, LocalStorage
from dataforge.storage.budget import GIB, DiskEstimate, DiskGuard, estimate
from dataforge.storage.parts import iter_documents
from dataforge.telemetry.logging import JsonFileLog, get_logger
from dataforge.telemetry.metrics import MemorySampler, os_peak_rss_bytes
from dataforge.tokenization.estimators import TokenEstimator, build_estimator
from dataforge.validation.dataset import validate_dataset

RunStatus = Literal["completed", "exists", "partial", "planned"]


@dataclass(slots=True)
class RunOptions:
    until: StageName | None = None
    resume: bool = True
    use_cache: bool = True
    force_modes: dict[str, StageMode] = field(default_factory=dict)
    dry_run: bool = False
    ignore_disk_budget: bool = False
    ingest_only: bool = False
    work_dir: Path | None = None
    output_dir: Path | None = None


@dataclass(slots=True)
class PreparedRun:
    loaded: LoadedPipelineConfig
    plan: PipelinePlan
    stages: dict[str, Stage]
    registry: SourceRegistry
    estimator: TokenEstimator
    work_dir: Path
    output_root: Path

    @property
    def readers(self) -> list[SourceReader]:
        return self.registry.readers()


@dataclass(slots=True)
class RunResult:
    status: RunStatus
    config_path: Path
    plan: PipelinePlan
    disk: DiskEstimate
    run_id: str | None = None
    run_dir: Path | None = None
    dataset_id: str | None = None
    dataset_dir: Path | None = None
    report_path: Path | None = None
    stats: list[StageStats] = field(default_factory=list)
    barrier_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    segments: list[SegmentOutcome] = field(default_factory=list)
    sources: list[SourceDescription] = field(default_factory=list)
    elapsed_s: float = 0.0
    peak_rss_bytes: int = 0
    expected_dataset_dir: Path | None = None


def prepare(config_path: Path, options: RunOptions | None = None) -> PreparedRun:
    """Load configuration, validate the plan, and instantiate stages (no data is read)."""
    options = options or RunOptions()
    loaded = load_pipeline_config(config_path)
    config = loaded.config
    plan = build_plan(config, options.until, ingest_only=options.ingest_only)
    estimator = build_estimator(config.tokenizer, loaded.base_dir)
    context = StageContext(loaded, estimator)
    stages: dict[str, Stage] = {}
    for node in plan.nodes:
        stage_config = config.stages.get(node.name)
        forced = options.force_modes.get(node.name)
        if forced is not None and forced != stage_config.mode:
            stage_config = stage_config.model_copy(update={"mode": forced})
        stages[node.name] = build_stage(node.name, stage_config, context)
    return PreparedRun(
        loaded=loaded,
        plan=plan,
        stages=stages,
        registry=SourceRegistry.from_pipeline(loaded),
        estimator=estimator,
        work_dir=options.work_dir or loaded.resolve(config.storage.work_dir),
        output_root=options.output_dir or loaded.resolve(config.storage.output_dir),
    )


def estimate_disk(prepared: PreparedRun) -> DiskEstimate:
    input_bytes = sum(reader.estimated_bytes() or 0 for reader in prepared.readers)
    storage = prepared.loaded.config.storage
    return estimate(
        input_bytes,
        prepared.plan.materializations,
        prepared.work_dir,
        int(storage.reserve_free_gb * GIB),
        keep_intermediates=storage.cleanup == "never",
    )


def _segment_fingerprints(prepared: PreparedRun, sources: list[SourceDescription], code_fp: str) -> list[str]:
    previous = combine_fingerprints(d.fingerprint for d in sources)
    fingerprints: list[str] = []
    for spec in prepared.plan.segments:
        payloads = [prepared.stages[name].fingerprint_payload() for name in spec.stage_names]
        layout = {
            "index": spec.index,
            "apply": spec.apply,
            "streaming": list(spec.streaming),
            "observe": spec.observe,
        }
        current = fingerprint({"previous": previous, "code": code_fp, "layout": layout, "stages": payloads})
        fingerprints.append(current)
        previous = current
    return fingerprints


def _ordered_stats(plan: PipelinePlan, segments: list[SegmentOutcome]) -> list[StageStats]:
    merged: dict[str, StageStats] = {}
    for outcome in segments:
        for name, stats in outcome.stats.items():
            if name in merged:
                merged[name].merge(stats)
            else:
                merged[name] = StageStats.from_dict(stats.as_dict())
    ordered = [merged[INGEST]] if INGEST in merged else []
    ordered.extend(merged[node.name] for node in plan.nodes if node.name in merged)
    return ordered


def _relative(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def _source_entries(
    prepared: PreparedRun, descriptions: list[SourceDescription], ingest: StageStats | None
) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    counters = ingest.counters if ingest is not None else {}
    for source, description in zip(prepared.loaded.sources, descriptions, strict=True):
        cfg = source.config
        errors = sum(v for k, v in counters.items() if k.startswith(f"errors|{source.source_id}|"))
        entries.append(
            SourceEntry(
                source_id=source.source_id,
                type=description.type,
                uri=description.uri,
                license=cfg.license,
                license_field=cfg.license_field,
                license_source=cfg.license_source,
                usage_policy=cfg.usage_policy,
                version=cfg.version,
                fingerprint=description.fingerprint,
                reproducible=description.reproducible,
                files=[FileEntry(path=f.relative, bytes=f.size, sha256=f.sha256) for f in description.files],
                documents_read=int(counters.get(f"documents|{source.source_id}", 0)),
                record_errors=int(errors),
                notes=list(description.notes),
            )
        )
    return entries


def _split_summaries(shards: list[ShardInfo]) -> tuple[SplitSummary, dict[str, SplitSummary]]:
    splits: dict[str, SplitSummary] = {}
    for split in sorted({s.split for s in shards}):
        members = [s for s in shards if s.split == split]
        splits[split] = SplitSummary(
            documents=sum(s.documents for s in members),
            bytes=sum(s.bytes for s in members),
            text_bytes=sum(s.text_bytes for s in members),
            tokens=sum(s.tokens for s in members),
            shards=len(members),
        )
    totals = SplitSummary(
        documents=sum(s.documents for s in shards),
        bytes=sum(s.bytes for s in shards),
        text_bytes=sum(s.text_bytes for s in shards),
        tokens=sum(s.tokens for s in shards),
        shards=len(shards),
    )
    return totals, splits


def run_pipeline(config_path: Path, options: RunOptions | None = None) -> RunResult:
    """Execute a pipeline configuration end to end (or up to ``options.until``)."""
    options = options or RunOptions()
    started = time.perf_counter()
    started_at = dt.datetime.now(dt.UTC)
    logger = get_logger("pipeline")
    prepared = prepare(config_path, options)
    loaded, plan = prepared.loaded, prepared.plan
    config = loaded.config
    disk = estimate_disk(prepared)
    result = RunResult(status="planned", config_path=loaded.path, plan=plan, disk=disk)
    if options.dry_run:
        return result
    if not disk.ok and not options.ignore_disk_budget:
        raise StorageBudgetExceeded(
            f"estimated disk need {disk.required_bytes / GIB:.2f} GiB exceeds free space "
            f"{disk.free_bytes / GIB:.2f} GiB minus reserve {disk.reserve_bytes / GIB:.2f} GiB",
            required_bytes=disk.required_bytes,
            available_bytes=disk.free_bytes,
        )

    descriptions = [reader.describe() for reader in prepared.readers]
    result.sources = descriptions
    code = code_identity()
    segment_fps = _segment_fingerprints(prepared, descriptions, code.fingerprint)
    dataset_fp = combine_fingerprints([loaded.config_hash, segment_fps[-1]])
    version = dataset_fp[:12]
    dataset_id = f"{config.pipeline.name}-{version}"
    dataset_relative = f"{config.pipeline.name}/{version}"
    dataset_dir = prepared.output_root / config.pipeline.name / version
    result.expected_dataset_dir = dataset_dir
    if plan.produces_dataset and (dataset_dir / MANIFEST_NAME).is_file():
        logger.info("dataset already exists (immutable); nothing to do", extra={"dataset_id": dataset_id})
        result.status = "exists"
        result.dataset_id = dataset_id
        result.dataset_dir = dataset_dir
        result.report_path = dataset_dir / "reports" / "report.md"
        return result

    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    run_dir = prepared.work_dir / "runs" / run_id
    result.run_id, result.run_dir = run_id, run_dir
    sampler = MemorySampler()
    guard = DiskGuard(prepared.work_dir, int(config.storage.reserve_free_gb * GIB))
    executor = SegmentExecutor(
        stages=prepared.stages,
        execution=config.execution,
        compression=config.storage.intermediate_compression,
        guard=guard,
        run_id=run_id,
        logger=logger,
        sampler=sampler,
    )
    with JsonFileLog(run_dir / "events.jsonl"):
        logger.info(
            "run started",
            extra={
                "run_id": run_id,
                "config": _relative(loaded.path, loaded.base_dir),
                "segments": len(plan.segments),
            },
        )
        source: Ingestor | list[Path] = Ingestor(prepared.readers)
        for spec, segment_fp in zip(plan.segments, segment_fps, strict=True):
            directory = prepared.work_dir / "segments" / f"seg{spec.index:02d}-{segment_fp[:16]}"
            logger.info(
                f"segment {spec.index}: {spec.label()}",
                extra={"run_id": run_id, "segment": spec.index},
            )
            outcome = executor.run(
                spec,
                segment_fp,
                directory,
                source,
                resume=options.resume,
                use_cache=options.use_cache,
            )
            result.segments.append(outcome)
            if outcome.barrier_summary is not None and spec.observe is not None:
                result.barrier_summaries[spec.observe] = outcome.barrier_summary
            source = outcome.parts
        result.stats = _ordered_stats(plan, result.segments)

        if not plan.produces_dataset:
            result.status = "partial"
            _finish_run(result, started, sampler, started_at, loaded)
            return result

        staging = prepared.work_dir / "staging" / run_id
        if staging.exists():
            shutil.rmtree(staging)
        writer = ShardWriter(staging, config.shard)
        for documents in iter_documents(result.segments[-1].parts, config.execution.batch_size):
            writer.write(documents)
        shards = writer.close()
        totals, splits = _split_summaries(shards)

        benchmarks = []
        for stage in prepared.stages.values():
            if isinstance(stage, ContaminationStage):
                benchmarks = stage.benchmark_infos()
        manifest = DatasetManifest(
            document_schema_version=DOCUMENT_SCHEMA_VERSION,
            dataset_id=dataset_id,
            name=config.pipeline.name,
            version=version,
            fingerprint=dataset_fp,
            created_at=started_at.isoformat(timespec="seconds"),
            run_id=run_id,
            code=CodeEntry(
                dataforge_version=code.version,
                git_sha=code.git_sha,
                git_dirty=code.git_dirty,
                code_fingerprint=code.fingerprint,
            ),
            pipeline=PipelineEntry(
                name=config.pipeline.name,
                config_path=_relative(loaded.path, loaded.base_dir),
                config_hash=loaded.config_hash,
                stage_order=[node.name for node in plan.nodes],
                config=loaded.hash_view(),
            ),
            tokenizer=prepared.estimator.name,
            sources=_source_entries(prepared, descriptions, next((s for s in result.stats if s.name == INGEST), None)),
            benchmarks=[
                BenchmarkEntry(
                    name=b.name,
                    path=b.path,
                    license=b.license,
                    source_url=b.source_url,
                    items=b.items,
                    probes=b.probes,
                    sha256=b.sha256,
                    available=b.available,
                )
                for b in benchmarks
            ],
            totals=totals,
            splits=splits,
            shards=[ShardEntry(**_shard_dict(s)) for s in shards],
            stages=[_stage_summary(s) for s in result.stats],
            reproduce={
                # Relative to the base directory the user runs from, not to storage.output_dir.
                "command": f"dataforge pipeline reproduce {_relative(dataset_dir, loaded.base_dir)}/{MANIFEST_NAME}",
                "rerun": f"dataforge pipeline run {_relative(loaded.path, loaded.base_dir)}",
                "notes": (
                    "Run from the pipeline base directory with the same DataForge code fingerprint and source files."
                ),
            },
        )
        result.elapsed_s = time.perf_counter() - started
        result.peak_rss_bytes = max(sampler.peak_bytes, os_peak_rss_bytes() or 0)

        report_context = ReportContext(
            dataset_dir=staging,
            manifest=manifest,
            loaded=loaded,
            plan=plan,
            stats=result.stats,
            barrier_summaries=result.barrier_summaries,
            segment_dirs=[s.directory for s in result.segments],
            elapsed_s=result.elapsed_s,
            peak_rss_bytes=result.peak_rss_bytes,
        )
        write_reports(report_context)
        write_manifest(staging / MANIFEST_NAME, manifest)
        validation = validate_dataset(
            staging,
            memory_limit_mb=config.execution.duckdb_memory_limit_mb,
            threads=config.execution.duckdb_threads,
            display_name=dataset_id,
        )
        (staging / "reports" / "validation.json").write_text(
            json.dumps(validation.as_dict(), indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        validation.raise_if_failed()
        published = LocalStorage(prepared.output_root).publish_directory(staging, dataset_relative)
        result.dataset_dir = Path(published)
        result.dataset_id = dataset_id
        result.report_path = result.dataset_dir / "reports" / "report.md"
        result.status = "completed"
        logger.info(
            "dataset published",
            extra={"run_id": run_id, "dataset_id": dataset_id, "documents": totals.documents},
        )
        if config.storage.cleanup == "on_success":
            for outcome in result.segments:
                shutil.rmtree(outcome.directory, ignore_errors=True)
        _finish_run(result, started, sampler, started_at, loaded)
    return result


def _shard_dict(shard: ShardInfo) -> dict[str, Any]:
    return {
        "path": shard.path,
        "split": shard.split,
        "format": shard.format,
        "compression": shard.compression,
        "documents": shard.documents,
        "bytes": shard.bytes,
        "text_bytes": shard.text_bytes,
        "tokens": shard.tokens,
        "sha256": shard.sha256,
    }


def _stage_summary(stats: StageStats) -> StageSummary:
    return StageSummary(
        name=stats.name,
        kind=stats.kind,
        mode=stats.mode,
        documents_in=stats.documents_in,
        documents_out=stats.documents_out,
        bytes_in=stats.bytes_in,
        bytes_out=stats.bytes_out,
        rejected=dict(sorted(stats.rejected.items())),
        flagged=dict(sorted(stats.flagged.items())),
        seconds=round(stats.seconds, 4),
    )


def _finish_run(
    result: RunResult,
    started: float,
    sampler: MemorySampler,
    started_at: dt.datetime,
    loaded: LoadedPipelineConfig,
) -> None:
    result.elapsed_s = time.perf_counter() - started
    result.peak_rss_bytes = max(result.peak_rss_bytes, sampler.peak_bytes, os_peak_rss_bytes() or 0)
    if result.run_dir is None:  # set before any segment runs
        raise PipelineError("run directory missing while finishing a run")
    payload: Mapping[str, Any] = {
        "run_id": result.run_id,
        "status": result.status,
        "config_path": _relative(loaded.path, loaded.base_dir),
        "started_at": started_at.isoformat(timespec="seconds"),
        "elapsed_s": round(result.elapsed_s, 3),
        "peak_rss_bytes": result.peak_rss_bytes,
        "dataset_id": result.dataset_id,
        "dataset_dir": _relative(result.dataset_dir, loaded.base_dir) if result.dataset_dir else None,
        "until": result.plan.until,
        "segments": [
            {
                "index": s.spec.index,
                "label": s.spec.label(),
                "fingerprint": s.fingerprint,
                "cached": s.cached,
                "resumed_checkpoints": s.resumed_checkpoints,
                "documents_out": s.documents_out,
                "seconds": round(s.seconds, 3),
            }
            for s in result.segments
        ],
        "stages": [s.as_dict() for s in result.stats],
        "barrier_summaries": result.barrier_summaries,
    }
    result.run_dir.mkdir(parents=True, exist_ok=True)
    (result.run_dir / "run.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
