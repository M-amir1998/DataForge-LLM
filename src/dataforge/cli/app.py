"""DataForge command-line interface."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from dataforge import __version__
from dataforge.cli import dataset, doctor, pipeline, sources
from dataforge.cli.common import console, ensure_safe_streams, handle_errors, simple_table
from dataforge.errors import ConfigError

app = typer.Typer(
    name="dataforge",
    help="Reproducible pretraining-data engineering: ingest, clean, deduplicate, govern, mix, and shard corpora.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)
report_app = typer.Typer(help="Show reports of pipeline runs.", invoke_without_command=True)
cache_app = typer.Typer(help="Manage the work area.", no_args_is_help=True)

WorkDirOpt = Annotated[Path, typer.Option("--work-dir", help="Work directory (storage.work_dir).")]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"dataforge {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """DataForge-LLM command line."""


app.command("doctor")(doctor.doctor)
app.command("ingest")(handle_errors(pipeline.ingest_command))
app.add_typer(sources.app, name="sources")
app.add_typer(sources.sample_app, name="sample")
app.add_typer(pipeline.app, name="pipeline")
app.add_typer(pipeline.dedup_app, name="dedup")
app.add_typer(pipeline.contamination_app, name="contamination")
app.add_typer(pipeline.mixture_app, name="mixture")
app.add_typer(dataset.app, name="dataset")
app.add_typer(dataset.quality_app, name="quality")
app.add_typer(dataset.privacy_app, name="privacy")
app.add_typer(report_app, name="report")
app.add_typer(cache_app, name="cache")


def _runs(work_dir: Path) -> list[Path]:
    runs_dir = work_dir / "runs"
    return sorted((p for p in runs_dir.glob("*/run.json")), key=lambda p: p.parent.name) if runs_dir.is_dir() else []


@report_app.callback()
@handle_errors
def report(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument(help="Run id, or 'latest'.")] = None,
    work_dir: WorkDirOpt = Path(".dataforge/work"),
    list_runs: Annotated[bool, typer.Option("--list", help="List recorded runs.")] = False,
) -> None:
    """Print the summary of a recorded run and the path of its full report."""
    if ctx.invoked_subcommand is not None:
        return
    runs = _runs(work_dir)
    if list_runs or run_id is None:
        rows = []
        for path in runs:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                [
                    data["run_id"],
                    data["status"],
                    data["config_path"],
                    f"{data['elapsed_s']:.1f}s",
                    data.get("dataset_id") or "-",
                ]
            )
        console.print(simple_table("Runs", ["Run", "Status", "Config", "Elapsed", "Dataset"], rows, justify="lllrl"))
        return
    if run_id == "latest":
        if not runs:
            raise ConfigError(f"no runs recorded under {work_dir}")
        path = runs[-1]
    else:
        path = work_dir / "runs" / run_id / "run.json"
        if not path.is_file():
            raise ConfigError(f"run '{run_id}' not found under {work_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        [
            s["name"],
            f"{s['documents_in'] or s['documents_out']:,}",
            f"{s['documents_out']:,}",
            ", ".join(f"{k}={v:,}" for k, v in sorted(s["rejected"].items(), key=lambda kv: -kv[1])[:3]) or "-",
        ]
        for s in data["stages"]
    ]
    console.print(
        f"[bold]{data['run_id']}[/] status={data['status']} config={data['config_path']} "
        f"elapsed={data['elapsed_s']:.1f}s"
    )
    console.print(simple_table("Stages", ["Stage", "In", "Out", "Top removals"], rows, justify="lrrl"))
    if data.get("dataset_dir"):
        console.print(f"dataset: {data['dataset_id']} at {data['dataset_dir']}")
        console.print(f"full report: {data['dataset_dir']}/reports/report.md")


@cache_app.command("clean")
@handle_errors
def cache_clean(
    work_dir: WorkDirOpt = Path(".dataforge/work"),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete intermediate segments and staging areas (run records are kept)."""
    targets = [work_dir / name for name in ("segments", "staging") if (work_dir / name).exists()]
    if not targets:
        console.print("nothing to clean")
        return
    if not yes and not typer.confirm(f"Delete {', '.join(str(t) for t in targets)}?"):
        raise typer.Exit(1)
    for target in targets:
        shutil.rmtree(target)
    console.print(f"removed {len(targets)} director{'y' if len(targets) == 1 else 'ies'}")


def main() -> None:
    ensure_safe_streams()
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
