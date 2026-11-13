"""Shared CLI plumbing: console, error mapping, and table rendering."""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, ParamSpec, TypeVar

import typer
from rich.console import Console, JustifyMethod
from rich.table import Table

from dataforge.errors import DataForgeError
from dataforge.pipelines.stage import StageStats

P = ParamSpec("P")
R = TypeVar("R")

#: Piped output (CI logs) gets a wide fixed width instead of Rich's 80-column default.
_WIDTH = None if sys.stdout.isatty() else 140
_JUSTIFY: dict[str, JustifyMethod] = {"l": "left", "r": "right", "c": "center"}
console = Console(highlight=False, soft_wrap=False, width=_WIDTH)
err_console = Console(stderr=True, highlight=False, width=_WIDTH)


def ensure_safe_streams() -> None:
    """Never crash on output: piped Windows consoles default to cp1252."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and encoding != "utf8" and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def handle_errors(func: Callable[P, R]) -> Callable[P, R]:
    """Map anticipated errors to clean messages and stable exit codes."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except DataForgeError as exc:
            err_console.print(f"[bold red]error:[/] {type(exc).__name__}: {exc}")
            problems = getattr(exc, "problems", None)
            if problems:
                for problem in problems:
                    err_console.print(f"  - {problem}")
            raise typer.Exit(exc.exit_code) from exc

    return wrapper


def print_json(payload: Any) -> None:
    console.print_json(json.dumps(payload, default=str))


def simple_table(
    title: str | None,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    justify: str | None = None,
) -> Table:
    table = Table(title=title, title_justify="left", show_lines=False, header_style="bold")
    justify = justify or ("l" + "r" * (len(headers) - 1))
    for header, mode in zip(headers, justify, strict=True):
        table.add_column(header, justify=_JUSTIFY[mode])
    for row in rows:
        table.add_row(*(str(cell) for cell in row))
    return table


def stage_table(stats: Sequence[StageStats]) -> Table:
    rows = []
    for s in stats:
        processed = s.documents_in or s.documents_out
        removed = s.documents_in - s.documents_out if s.documents_in else 0
        reasons = ", ".join(f"{reason}={count:,}" for reason, count in s.rejected.most_common(3))
        flagged = sum(s.flagged.values())
        if flagged:
            reasons = (reasons + ", " if reasons else "") + f"flagged={flagged:,}"
        rows.append(
            [
                s.name,
                s.kind,
                f"{processed:,}",
                f"{s.documents_out:,}",
                f"{removed:,}",
                f"{s.seconds:.2f}",
                reasons or "-",
            ]
        )
    return simple_table(
        "Stages",
        ["Stage", "Kind", "In", "Out", "Removed", "Seconds", "Reasons"],
        rows,
        justify="llrrrrl",
    )
