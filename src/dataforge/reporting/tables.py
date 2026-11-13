"""Markdown table and number formatting helpers."""

from __future__ import annotations

from collections.abc import Sequence


def fmt_int(value: float) -> str:
    return f"{int(value):,}"


def fmt_bytes(value: float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.2f} {unit}"
        size /= 1024
    return f"{size:,.2f} TB"


def fmt_pct(numerator: float, denominator: float) -> str:
    if not denominator:
        return "0.0%"
    return f"{100.0 * numerator / denominator:.1f}%"


def md_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def md_table(headers: Sequence[str], rows: Sequence[Sequence[object]], align: str | None = None) -> str:
    """Render a GitHub-flavoured Markdown table; ``align`` is a string of l/r/c per column."""
    align = align or "l" + "r" * (len(headers) - 1)
    marks = {"l": ":---", "r": "---:", "c": ":---:"}
    lines = [
        "| " + " | ".join(md_escape(h) for h in headers) + " |",
        "| " + " | ".join(marks[a] for a in align) + " |",
    ]
    lines.extend("| " + " | ".join(md_escape(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)
