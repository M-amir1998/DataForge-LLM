"""``dataforge pipeline|ingest|dedup|contamination|mixture`` commands."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated, Any

import typer

from dataforge.cli.common import console, handle_errors, simple_table, stage_table
from dataforge.config.models import DEFAULT_STAGE_ORDER, StageName
from dataforge.errors import ConfigError, ReproducibilityError
from dataforge.pipelines.runner import (
    PreparedRun,
    RunOptions,
    RunResult,
    estimate_disk,
    prepare,
    run_pipeline,
)
from dataforge.provenance.lineage import code_identity
from dataforge.provenance.manifest import read_manifest
from dataforge.reporting.report import iter_artifact
from dataforge.reporting.tables import fmt_bytes
from dataforge.telemetry.logging import configure_logging

app = typer.Typer(help="Plan, run, and reproduce pipelines.", no_args_is_help=True)
dedup_app = typer.Typer(help="Run the pipeline up to a deduplication stage and report it.", no_args_is_help=True)
contamination_app = typer.Typer(help="Benchmark contamination scans.", no_args_is_help=True)
mixture_app = typer.Typer(help="Dataset mixture construction.", no_args_is_help=True)

ConfigArg = Annotated[Path, typer.Argument(help="Pipeline configuration YAML.", exists=True, dir_okay=False)]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="Only print the final summary.")]


_STAGES: dict[str, StageName] = {name: name for name in DEFAULT_STAGE_ORDER}


def _stage_name(value: str | None) -> StageName | None:
    if value is None:
        return None
    if value not in _STAGES:
        raise ConfigError(f"unknown stage '{value}'; choose from {', '.join(DEFAULT_STAGE_ORDER)}")
    return _STAGES[value]


def _render_plan(prepared: PreparedRun) -> None:
    plan = prepared.plan
    rows = [[n.name, n.kind, n.mode, ", ".join(n.requires) or "-", n.description] for n in plan.nodes]
    console.print(
        simple_table(
            f"Pipeline '{prepared.loaded.config.pipeline.name}' - stage DAG",
            ["Stage", "Kind", "Mode", "Requires", "Description"],
            rows,
            justify="lllll",
        )
    )
    segment_rows = [[str(s.index), s.label()] for s in plan.segments]
    console.print(
        simple_table(
            "Execution segments (each ends in one materialization)",
            ["#", "Fused operators"],
            segment_rows,
            justify="ll",
        )
    )
    if plan.disabled:
        console.print(f"Disabled stages: {', '.join(plan.disabled)}")
    console.print(f"Config hash: {prepared.loaded.config_hash[:16]}  |  materializations: {plan.materializations}")


def _render_sources(prepared: PreparedRun) -> None:
    rows = []
    for source, reader in zip(prepared.loaded.sources, prepared.readers, strict=True):
        size = reader.estimated_bytes()
        rows.append(
            [
                source.source_id,
                source.config.type,
                source.config.license
                if not source.config.license_field
                else f"per record ({source.config.license_field})",
                fmt_bytes(size) if size is not None else "unknown",
            ]
        )
    console.print(simple_table("Sources", ["Source", "Type", "License", "Size"], rows, justify="lllr"))


def _render_result(result: RunResult) -> None:
    if result.stats:
        console.print(stage_table(result.stats))
    for segment in result.segments:
        if segment.cached:
            console.print(f"segment {segment.spec.index}: cache hit ({segment.spec.label()})")
        elif segment.resumed_checkpoints:
            console.print(f"segment {segment.spec.index}: resumed after {segment.resumed_checkpoints} checkpoint(s)")
    console.print(
        f"status: [bold]{result.status}[/]  elapsed {result.elapsed_s:.2f}s  "
        f"peak RSS {result.peak_rss_bytes / 2**20:,.0f} MB"
    )
    if result.run_id:
        console.print(f"run id: {result.run_id}")
    if result.dataset_dir:
        console.print(f"dataset: {result.dataset_id}\n  path: {result.dataset_dir}\n  report: {result.report_path}")


def _run(config: Path, options: RunOptions, quiet: bool) -> RunResult:
    configure_logging("WARNING" if quiet else "INFO")
    result = run_pipeline(config, options)
    _render_result(result)
    return result


@app.command("plan")
@handle_errors
def plan_command(config: ConfigArg) -> None:
    """Validate a configuration and display the execution DAG without reading data."""
    prepared = prepare(config)
    _render_plan(prepared)


@app.command("run")
@handle_errors
def run_command(
    config: ConfigArg,
    *,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show sources, stages, outputs, and disk estimate only."),
    ] = False,
    until: Annotated[str | None, typer.Option(help="Stop after this stage (no dataset is produced).")] = None,
    resume: Annotated[bool, typer.Option("--resume/--no-resume", help="Resume interrupted segments.")] = True,
    cache: Annotated[bool, typer.Option("--cache/--no-cache", help="Reuse completed, unchanged segments.")] = True,
    ignore_disk_budget: Annotated[bool, typer.Option(help="Run even if the disk estimate exceeds the budget.")] = False,
    quiet: QuietOpt = False,
) -> None:
    """Run a pipeline end to end and publish an immutable dataset version."""
    options = RunOptions(
        until=_stage_name(until), resume=resume, use_cache=cache, ignore_disk_budget=ignore_disk_budget
    )
    if dry_run:
        prepared = prepare(config, options)
        _render_sources(prepared)
        _render_plan(prepared)
        disk = estimate_disk(prepared)
        cfg = prepared.loaded.config
        console.print(
            simple_table(
                "Disk estimate",
                ["Input", "Temporary", "Output", "Free", "Reserve", "OK"],
                [
                    [
                        fmt_bytes(disk.input_bytes),
                        fmt_bytes(disk.temp_bytes),
                        fmt_bytes(disk.output_bytes),
                        fmt_bytes(disk.free_bytes),
                        fmt_bytes(disk.reserve_bytes),
                        "yes" if disk.ok else "NO",
                    ]
                ],
            )
        )
        console.print(
            f"Expected output: {prepared.output_root / cfg.pipeline.name}/<content fingerprint>/ "
            "(manifest.json, DATASET_CARD.md, data/*, reports/report.md)"
        )
        return
    _run(config, options, quiet)


@app.command("reproduce")
@handle_errors
def reproduce_command(
    manifest_path: Annotated[Path, typer.Argument(help="manifest.json of the dataset to reproduce.", exists=True)],
    config: Annotated[
        Path | None,
        typer.Option(help="Pipeline config (default: the path recorded in the manifest)."),
    ] = None,
    output_dir: Annotated[Path | None, typer.Option(help="Where to build the reproduction.")] = None,
) -> None:
    """Rebuild a dataset from scratch and compare it byte-for-byte with its manifest."""
    manifest = read_manifest(manifest_path)
    config_path = config or Path(manifest.pipeline.config_path)
    if not config_path.is_file():
        raise ConfigError(
            f"pipeline config not found: {config_path} (run from the pipeline base directory or pass --config)"
        )
    prepared = prepare(config_path)
    if prepared.loaded.config_hash != manifest.pipeline.config_hash:
        raise ReproducibilityError(
            "the pipeline config differs from the one recorded in the manifest (config hash mismatch)"
        )
    code = code_identity()
    if code.fingerprint != manifest.code.code_fingerprint:
        console.print("[yellow]warning:[/] code fingerprint differs from the manifest; outputs may differ")
    target = output_dir or prepared.work_dir / "reproduce" / manifest.version
    configure_logging("WARNING")
    result = run_pipeline(
        config_path,
        RunOptions(use_cache=False, resume=False, output_dir=target, work_dir=target / "_work"),
    )
    if result.dataset_dir is None:
        raise ReproducibilityError("the reproduction run did not produce a dataset")
    reproduced = read_manifest(result.dataset_dir / "manifest.json")
    original = {s.path: s.sha256 for s in manifest.shards}
    rebuilt = {s.path: s.sha256 for s in reproduced.shards}
    mismatched = sorted(path for path in original.keys() | rebuilt.keys() if original.get(path) != rebuilt.get(path))
    rows = [
        ["fingerprint", manifest.fingerprint[:16], reproduced.fingerprint[:16]],
        ["documents", f"{manifest.totals.documents:,}", f"{reproduced.totals.documents:,}"],
        ["shards", str(len(original)), str(len(rebuilt))],
    ]
    console.print(simple_table("Reproduction", ["Field", "Manifest", "Rebuilt"], rows, justify="lll"))
    if mismatched:
        raise ReproducibilityError(f"{len(mismatched)} shard(s) differ: {mismatched[:5]}")
    console.print(f"[green]bit-identical[/]: all {len(original)} shards match their recorded SHA-256 digests")


def _partial(config: Path, until: StageName, quiet: bool, **kwargs: Any) -> RunResult:
    return _run(config, RunOptions(until=until, **kwargs), quiet)


def ingest_command(config: ConfigArg, quiet: QuietOpt = False) -> None:
    """Stream all sources into the work area (parse, identify, stamp provenance)."""
    result = _run(config, RunOptions(ingest_only=True), quiet)
    ingest = next((s for s in result.stats if s.name == "ingest"), None)
    if ingest is not None:
        per_source: dict[str, Counter[str]] = defaultdict(Counter)
        for key, value in ingest.counters.items():
            kind, source, *rest = key.split("|")
            per_source[source][kind if not rest else f"error:{rest[0]}"] += value
        rows = [
            [
                source,
                f"{c['documents']:,}",
                fmt_bytes(c["bytes"]),
                ", ".join(f"{k[6:]}={v}" for k, v in c.items() if k.startswith("error:")) or "-",
            ]
            for source, c in per_source.items()
        ]
        console.print(
            simple_table(
                "Input statistics",
                ["Source", "Documents", "Bytes", "Record errors"],
                rows,
                justify="lrrl",
            )
        )


def _barrier_summary(result: RunResult, stage: str) -> None:
    summary = result.barrier_summaries.get(stage)
    if summary:
        rows = [[key, f"{value:,}" if isinstance(value, int) else str(value)] for key, value in summary.items()]
        console.print(simple_table(f"{stage} summary", ["Metric", "Value"], rows))


@dedup_app.command("exact")
@handle_errors
def dedup_exact(config: ConfigArg, quiet: QuietOpt = False) -> None:
    """Run up to exact deduplication and report duplicate groups."""
    _barrier_summary(_partial(config, "exact_dedup", quiet), "exact_dedup")


@dedup_app.command("near")
@handle_errors
def dedup_near(config: ConfigArg, quiet: QuietOpt = False) -> None:
    """Run up to near-duplicate detection and report clusters."""
    _barrier_summary(_partial(config, "near_dedup", quiet), "near_dedup")


@contamination_app.command("scan")
@handle_errors
def contamination_scan(config: ConfigArg, quiet: QuietOpt = False) -> None:
    """Scan the corpus for benchmark overlap without removing anything (annotate mode)."""
    result = _partial(config, "contamination", quiet, force_modes={"contamination": "annotate"})
    exact: Counter[str] = Counter()
    near: Counter[str] = Counter()
    documents: dict[str, set[str]] = defaultdict(set)
    for row in iter_artifact([s.directory for s in result.segments], "contamination_matches"):
        (exact if row["method"] in {"exact", "substring"} else near)[row["benchmark"]] += 1
        documents[row["benchmark"]].add(row["document_id"])
    rows = [[b, f"{exact[b]:,}", f"{near[b]:,}", f"{len(documents[b]):,}"] for b in sorted(documents)]
    console.print(simple_table("Contamination", ["Benchmark", "Exact", "Near", "Documents affected"], rows))
    if not rows:
        console.print("No benchmark overlap found.")


@mixture_app.command("build")
@handle_errors
def mixture_build(config: ConfigArg, quiet: QuietOpt = False) -> None:
    """Run up to the mixture stage and report target vs achieved weights."""
    result = _partial(config, "mixture", quiet)
    plan = result.barrier_summaries.get("mixture")
    if not plan:
        return
    rows = [
        [
            d["domain"],
            f"{d['target_weight']:.1%}",
            f"{d['achieved_weight']:.1%}",
            f"{d['selected_documents']:,}",
            f"{d['sampling_rate']:.1%}",
        ]
        for d in plan["domains"]
    ]
    console.print(
        simple_table(f"Mixture ({plan['unit']})", ["Domain", "Target", "Achieved", "Documents", "Rate"], rows)
    )
    for note in plan.get("notes", []):
        console.print(f"note: {note}")
    console.print(json.dumps({"total_target": plan["total_target"]}))
