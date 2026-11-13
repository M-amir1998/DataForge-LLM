"""Dataset analytics over output shards with DuckDB (bounded memory, spills to disk)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from dataforge.provenance.manifest import DatasetManifest

LENGTH_EDGES: tuple[int, ...] = (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)


def _quote(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def relation_sql(dataset_dir: Path, manifest: DatasetManifest) -> str | None:
    """A DuckDB table expression over all shards, or ``None`` for an empty dataset."""
    parquet = [dataset_dir / s.path for s in manifest.shards if s.format == "parquet"]
    jsonl = [dataset_dir / s.path for s in manifest.shards if s.format == "jsonl"]
    if parquet:
        return "read_parquet([" + ", ".join(_quote(p) for p in parquet) + "])"
    if jsonl:
        return (
            "read_json_auto([" + ", ".join(_quote(p) for p in jsonl) + "], format='newline_delimited', "
            "compression='zstd')"
        )
    return None


class DatasetAnalytics:
    def __init__(
        self,
        dataset_dir: Path,
        manifest: DatasetManifest,
        *,
        memory_limit_mb: int = 512,
        threads: int = 2,
    ) -> None:
        self.relation = relation_sql(dataset_dir, manifest)
        self.connection = duckdb.connect(":memory:")
        self.connection.execute(f"SET memory_limit = '{int(memory_limit_mb)}MB'")
        self.connection.execute(f"SET threads = {int(threads)}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DatasetAnalytics:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        if self.relation is None:
            return []
        return self.connection.execute(sql.replace("{data}", self.relation)).fetchall()

    def grouped(self, column: str, missing: str = "'unknown'") -> list[tuple[str, int, int, int]]:
        """(value, documents, text bytes, tokens) per distinct value of ``column``.

        ``missing`` is a SQL expression used when the column is NULL.
        """
        # ``column`` and ``missing`` are code-supplied identifiers/expressions, never user input.
        rows = self.query(
            f"SELECT coalesce(CAST({column} AS VARCHAR), {missing}) AS k, count(*), sum(num_bytes), "  # noqa: S608
            f"sum(coalesce(token_estimate, 0)) FROM {{data}} GROUP BY k ORDER BY count(*) DESC, k"
        )
        return [(str(r[0]), int(r[1]), int(r[2] or 0), int(r[3] or 0)) for r in rows]

    def length_histogram(self, edges: Sequence[int] = LENGTH_EDGES) -> list[tuple[str, int]]:
        cases = " ".join(f"WHEN num_chars <= {edge} THEN {index}" for index, edge in enumerate(edges))
        sql = f"SELECT CASE {cases} ELSE {len(edges)} END AS b, count(*) FROM {{data}} GROUP BY b"  # noqa: S608
        rows = dict(self.query(sql))
        labels = [f"<={edge:,}" for edge in edges] + [f">{edges[-1]:,}"]
        return [(label, int(rows.get(index, 0))) for index, label in enumerate(labels)]

    def quality_histogram(self) -> list[tuple[str, int]]:
        rows = dict(
            self.query(
                "SELECT least(9, CAST(floor(quality_score * 10) AS INTEGER)) AS b, count(*) FROM {data} "
                "WHERE quality_score IS NOT NULL GROUP BY b"
            )
        )
        return [(f"{index / 10:.1f}", int(rows.get(index, 0))) for index in range(10)]

    def length_stats(self) -> dict[str, float]:
        rows = self.query(
            "SELECT count(*), avg(num_chars), median(num_chars), quantile_cont(num_chars, 0.9), "
            "max(num_chars), sum(coalesce(token_estimate, 0)) FROM {data}"
        )
        if not rows or rows[0][0] == 0:
            return {}
        count, mean, median, p90, longest, tokens = rows[0]
        return {
            "documents": float(count),
            "mean_chars": float(mean),
            "median_chars": float(median),
            "p90_chars": float(p90),
            "max_chars": float(longest),
            "tokens": float(tokens),
        }
