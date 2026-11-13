"""``dataforge dataset|quality|privacy`` commands (operate on produced datasets)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dataforge.cli.common import console, handle_errors, print_json, simple_table
from dataforge.config.loader import load_pipeline_config
from dataforge.config.models import PIIConfig, QualityConfig, SecretsConfig
from dataforge.inspection import (
    analyze_quality,
    inspect_dataset,
    run_sql,
    scan_privacy,
)
from dataforge.reporting.tables import fmt_bytes, fmt_pct
from dataforge.validation.dataset import validate_dataset

app = typer.Typer(help="Validate, inspect, and query datasets.", no_args_is_help=True)
quality_app = typer.Typer(help="Quality analysis.", no_args_is_help=True)
privacy_app = typer.Typer(help="Privacy scans.", no_args_is_help=True)

DatasetArg = Annotated[
    Path,
    typer.Argument(help="Dataset directory (with manifest.json) or Parquet files.", exists=True),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Take thresholds from this pipeline config.")]


@app.command("validate")
@handle_errors
def validate_command(
    dataset: DatasetArg,
    hashes: Annotated[bool, typer.Option("--hashes/--no-hashes", help="Verify shard SHA-256 digests.")] = True,
    as_json: JsonOpt = False,
) -> None:
    """Check manifest, hashes, row counts, schema, id uniqueness, and split leakage."""
    # A broken manifest is a validation failure (exit 5), so do not parse it up front.
    root = dataset.parent if dataset.name == "manifest.json" else dataset
    report = validate_dataset(root, check_hashes=hashes)
    if as_json:
        print_json(report.as_dict())
    else:
        rows = [[c.name, "ok" if c.ok else "FAIL", c.detail] for c in report.checks]
        console.print(simple_table(f"Validation of {root.name}", ["Check", "Result", "Detail"], rows, justify="lcl"))
    report.raise_if_failed()


@app.command("inspect")
@handle_errors
def inspect_command(dataset: DatasetArg, as_json: JsonOpt = False) -> None:
    """Summarize documents, bytes, tokens, sources, licenses, languages, domains, quality, duplicates."""
    summary = inspect_dataset(dataset)
    if as_json:
        print_json(summary)
        return
    total = summary["documents"]
    console.print(f"[bold]{summary['dataset_id']}[/] created {summary['created_at']}")
    console.print(
        f"documents {total:,}  text {fmt_bytes(summary['text_bytes'])}  shards {fmt_bytes(summary['shard_bytes'])}  "
        f"est. tokens {summary['estimated_tokens']:,}  splits {summary['splits']}"
    )
    for key, title in (
        ("sources", "Sources"),
        ("licenses", "Licenses"),
        ("languages", "Languages"),
        ("domains", "Domains"),
    ):
        rows = [[k, f"{d:,}", fmt_pct(d, total), f"{t:,}"] for k, d, _, t in summary[key]]
        console.print(simple_table(title, [title[:-1], "Documents", "Share", "Est. tokens"], rows))
    bins = summary["quality_histogram"]
    console.print(
        simple_table(
            "Quality score distribution",
            ["Score bin", "Documents"],
            [[b, f"{n:,}"] for b, n in bins],
        )
    )
    console.print(
        simple_table(
            "Duplicate statistics",
            ["Metric", "Value"],
            [[k, f"{v:,}"] for k, v in summary["duplicates"].items()],
        )
    )


@app.command("sql")
@handle_errors
def sql_command(
    dataset: DatasetArg,
    query: Annotated[str, typer.Argument(help="A single SELECT; the dataset is the view `docs`.")],
    max_rows: Annotated[int, typer.Option(help="Maximum rows to print.")] = 50,
) -> None:
    """Run a read-only SQL query (DuckDB) against the dataset."""
    columns, rows = run_sql(dataset, query, max_rows=max_rows)
    console.print(
        simple_table(
            None,
            columns,
            [[("NULL" if v is None else v) for v in row] for row in rows],
            justify="l" * len(columns),
        )
    )


def _stage_configs(config: Path | None) -> tuple[QualityConfig, PIIConfig, SecretsConfig]:
    if config is None:
        return QualityConfig(), PIIConfig(), SecretsConfig()
    stages = load_pipeline_config(config).config.stages
    return stages.quality, stages.pii, stages.secrets


@quality_app.command("analyze")
@handle_errors
def quality_analyze(dataset: DatasetArg, config: ConfigOpt = None, as_json: JsonOpt = False) -> None:
    """Score every document with the quality signals and report pass rates."""
    quality, _, _ = _stage_configs(config)
    analysis = analyze_quality(dataset, quality)
    if as_json:
        print_json(analysis.as_dict())
        return
    rows = [
        [
            name,
            f"{analysis.evaluated[name]:,}",
            f"{analysis.failed[name]:,}",
            fmt_pct(analysis.failed[name], analysis.evaluated[name]),
        ]
        for name in sorted(analysis.evaluated, key=lambda n: -analysis.failed[n])
    ]
    console.print(
        simple_table(
            f"Quality signals over {analysis.documents:,} documents",
            ["Signal", "Evaluated", "Failed", "Rate"],
            rows,
        )
    )
    console.print(
        f"would pass the configured policy: {analysis.passed:,} ({fmt_pct(analysis.passed, analysis.documents)})"
    )


@privacy_app.command("scan")
@handle_errors
def privacy_scan(dataset: DatasetArg, config: ConfigOpt = None, as_json: JsonOpt = False) -> None:
    """Count PII and credential detections by type (values are never printed)."""
    _, pii, secrets = _stage_configs(config)
    scan = scan_privacy(dataset, pii, secrets)
    if as_json:
        print_json(scan.as_dict())
        return
    rows = [
        [kind, f"{count:,}", f"{scan.documents_by_type[kind]:,}"] for kind, count in sorted(scan.detections.items())
    ]
    console.print(
        simple_table(
            f"Detections over {scan.documents:,} documents",
            ["Type", "Detections", "Documents"],
            rows,
        )
    )
    console.print(f"documents with PII: {scan.documents_with_pii:,}; with credentials: {scan.documents_with_secrets:,}")
