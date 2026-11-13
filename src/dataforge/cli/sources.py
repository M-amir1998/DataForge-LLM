"""``dataforge sources`` and ``dataforge sample`` commands."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Annotated

import typer

from dataforge.cli.common import console, handle_errors, simple_table
from dataforge.config.models import PIIConfig, SecretsConfig
from dataforge.privacy.pii import PIIScanner
from dataforge.privacy.secrets import build_secret_scanner
from dataforge.privacy.spans import redact
from dataforge.reporting.tables import fmt_bytes
from dataforge.sources.base import RawRecord, RecordError
from dataforge.sources.registry import SourceRegistry
from dataforge.synthetic.corpus import generate_sample_corpus

app = typer.Typer(help="Inspect the source registry.", no_args_is_help=True)
sample_app = typer.Typer(help="Synthetic sample data.", no_args_is_help=True)

RegistryOpt = Annotated[
    Path,
    typer.Option("--registry", "-r", help="Source registry YAML.", exists=True, dir_okay=False),
]
DEFAULT_REGISTRY = Path("configs/sources/demo.yaml")


@app.command("list")
@handle_errors
def list_sources(registry: RegistryOpt = DEFAULT_REGISTRY) -> None:
    """List registered sources with type, license, and declared metadata."""
    sources = SourceRegistry.from_file(registry)
    rows = []
    for source in sources:
        cfg = source.config
        license_text = f"per record ({cfg.license_field})" if cfg.license_field else cfg.license
        location = getattr(cfg, "path", None) or getattr(cfg, "dataset", None) or "-"
        rows.append(
            [
                source.source_id,
                cfg.type,
                str(location),
                license_text,
                cfg.usage_policy,
                cfg.domain_hint or "-",
            ]
        )
    console.print(
        simple_table(
            f"Sources in {registry.as_posix()}",
            ["Source", "Type", "Location", "License", "Usage", "Domain hint"],
            rows,
            justify="llllll",
        )
    )


@app.command("inspect")
@handle_errors
def inspect_source(
    source_id: Annotated[str, typer.Argument(help="Source id from the registry.")],
    registry: RegistryOpt = DEFAULT_REGISTRY,
    sample: Annotated[int, typer.Option(help="Number of records to preview.")] = 3,
    fingerprint: Annotated[
        bool,
        typer.Option("--fingerprint/--no-fingerprint", help="Hash every file (reads all bytes)."),
    ] = True,
) -> None:
    """Describe one source, fingerprint its content, and preview records (PII/secrets redacted)."""
    sources = SourceRegistry.from_file(registry)
    reader = sources.reader(source_id)
    cfg = reader.config
    console.print(f"[bold]{source_id}[/] ({cfg.type}) - {cfg.description or 'no description'}")
    console.print(
        f"license: {cfg.license} (field: {cfg.license_field or '-'}; "
        f"evidence: {cfg.license_source or '-'}); usage: {cfg.usage_policy}"
    )
    if fingerprint:
        description = reader.describe()
        size = fmt_bytes(description.total_bytes) if description.total_bytes is not None else "unknown"
        console.print(
            f"files: {len(description.files)}  size: {size}  fingerprint: {description.fingerprint[:16]}  "
            f"reproducible: {description.reproducible}"
        )
        for note in description.notes:
            console.print(f"note: {note}")
    pii = PIIScanner(PIIConfig().detectors)
    secrets = build_secret_scanner(SecretsConfig().detectors, 3.5, 12)
    errors = 0
    previews: list[list[str]] = []
    for item in itertools.islice(reader.iter_records(), sample * 10):
        if isinstance(item, RecordError):
            errors += 1
            continue
        if isinstance(item, RawRecord) and len(previews) < sample:
            text = item.content[:240]
            preview = redact(text, [*pii.scan(text), *secrets.scan(text)]).replace("\n", " ")
            previews.append([item.locator, f"{len(item.content):,}", preview[:120]])
    console.print(
        simple_table(
            "Preview (PII and credentials redacted)",
            ["Locator", "Chars", "Text"],
            previews,
            justify="lrl",
        )
    )
    if errors:
        console.print(f"record errors in the previewed range: {errors}")


@sample_app.command("generate")
@handle_errors
def generate(
    output: Annotated[Path, typer.Argument(help="Directory to write the corpus into.")],
    scale: Annotated[float, typer.Option(help="Multiply every planted count by this factor.")] = 1.0,
    seed: Annotated[int, typer.Option(help="Random seed (deterministic output).")] = 7,
) -> None:
    """Generate the deterministic synthetic sample corpus with ground-truth labels."""
    summary = generate_sample_corpus(output, seed=seed, scale=scale)
    console.print(
        f"wrote {summary.records:,} records in {summary.files:,} files ({fmt_bytes(summary.bytes)}) to {output}"
    )
    console.print(
        simple_table(
            "Planted phenomena",
            ["Phenomenon", "Records"],
            [[k, f"{v:,}"] for k, v in summary.phenomena.items()],
        )
    )
