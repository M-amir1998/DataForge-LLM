"""Dataset integrity validation.

A dataset is accepted only if every check passes: manifest schema, shard existence, unique
shard paths, SHA-256 digests, row counts, Arrow schema, totals, unique document ids, no
group leakage across splits, and a content-hash spot check. Aggregations run in DuckDB,
which streams Parquet and spills to disk, so validation memory stays bounded.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.domain.hashing import content_hash, sha256_file
from dataforge.errors import DatasetValidationError, InvalidManifest
from dataforge.provenance.manifest import DatasetManifest, read_manifest
from dataforge.storage.schema import SCHEMA_VERSION_KEY, schema_matches

_SPOT_CHECK_ROWS: Final = 500
#: Groups (near cluster > exact group > content hash > id) present in more than one split.
_LEAKAGE_SQL: Final = """
SELECT count(*) FROM (
    SELECT coalesce(near_dup_cluster, exact_dup_group, content_hash, document_id) AS grp
    FROM {data}
    GROUP BY grp
    HAVING count(DISTINCT split) > 1
)
"""


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(slots=True)
class ValidationReport:
    dataset: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def problems(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok]

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(CheckResult(name, ok, detail))

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise DatasetValidationError(f"dataset {self.dataset} failed validation", self.problems)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _connect(memory_limit_mb: int, threads: int) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET memory_limit = '{int(memory_limit_mb)}MB'")
    connection.execute(f"SET threads = {int(threads)}")
    return connection


def _parquet_list(paths: list[Path]) -> str:
    return "[" + ", ".join(f"'{_sql_path(p)}'" for p in paths) + "]"


_KEY_COLUMNS: Final = "document_id, split, near_dup_cluster, exact_dup_group, content_hash"
_JSON_COLUMNS: Final = (
    "{'document_id': 'VARCHAR', 'split': 'VARCHAR', 'near_dup_cluster': 'VARCHAR', "
    "'exact_dup_group': 'VARCHAR', 'content_hash': 'VARCHAR'}"
)


def _sample_text_hashes(path: Path) -> list[tuple[str, str]]:
    sample = pq.ParquetFile(path)
    try:
        batch = next(sample.iter_batches(batch_size=_SPOT_CHECK_ROWS, columns=["text", "content_hash"]), None)
    finally:
        sample.close()
    if batch is None:
        return []
    return list(zip(batch.column("text").to_pylist(), batch.column("content_hash").to_pylist(), strict=True))


def _sample_jsonl_hashes(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in iter_zstd_lines(path):
        if line.strip():
            row = json.loads(line)
            pairs.append((row["text"], row["content_hash"]))
            if len(pairs) >= _SPOT_CHECK_ROWS:
                break
    return pairs


def _check_parquet_shard(path: Path, expected_rows: int) -> tuple[bool, str]:
    parquet = pq.ParquetFile(path)
    try:
        rows = parquet.metadata.num_rows
        schema = parquet.schema_arrow
    finally:
        parquet.close()
    if rows != expected_rows:
        return False, f"row count {rows} != manifest {expected_rows}"
    if not schema_matches(schema):
        return False, "Arrow schema differs from the canonical document schema"
    if (schema.metadata or {}).get(SCHEMA_VERSION_KEY) is None:
        return False, "missing document schema version metadata"
    return True, "ok"


def iter_zstd_lines(path: Path, chunk_bytes: int = 1 << 20) -> Iterator[bytes]:
    """Stream lines from a zstd-compressed file in bounded memory."""
    with pa.CompressedInputStream(str(path), "zstd") as stream:
        pending = b""
        while True:
            chunk = stream.read(chunk_bytes)
            if not chunk:
                break
            pending += chunk
            *lines, pending = pending.split(b"\n")
            yield from lines
        if pending:
            yield pending


def _check_jsonl_shard(path: Path, expected_rows: int) -> tuple[bool, str]:
    rows = 0
    for line in iter_zstd_lines(path):
        if line.strip():
            json.loads(line)
            rows += 1
    if rows != expected_rows:
        return False, f"row count {rows} != manifest {expected_rows}"
    return True, "ok"


def validate_dataset(
    dataset_dir: Path,
    *,
    check_hashes: bool = True,
    memory_limit_mb: int = 512,
    threads: int = 2,
    hasher: Callable[[Path], str] = sha256_file,
    display_name: str | None = None,
) -> ValidationReport:
    """Validate a dataset directory (containing ``manifest.json``).

    ``display_name`` is what the report shows; it defaults to the directory name so that
    reports never embed machine-specific absolute paths.
    """
    report = ValidationReport(display_name or dataset_dir.name)
    try:
        manifest = read_manifest(dataset_dir / "manifest.json")
    except InvalidManifest as exc:
        report.add("manifest", False, str(exc))
        return report
    report.add("manifest", True, f"schema {manifest.schema_version}, {len(manifest.shards)} shards")
    _validate_with_manifest(
        dataset_dir,
        manifest,
        report,
        check_hashes=check_hashes,
        memory_limit_mb=memory_limit_mb,
        threads=threads,
        hasher=hasher,
    )
    return report


def _validate_with_manifest(
    dataset_dir: Path,
    manifest: DatasetManifest,
    report: ValidationReport,
    *,
    check_hashes: bool,
    memory_limit_mb: int,
    threads: int,
    hasher: Callable[[Path], str],
) -> None:
    paths = [shard.path for shard in manifest.shards]
    names = [Path(p).name for p in paths]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    report.add(
        "unique_shard_names",
        not duplicates,
        "ok" if not duplicates else f"duplicates: {duplicates}",
    )

    missing = [p for p in paths if not (dataset_dir / p).is_file()]
    report.add("shards_exist", not missing, "ok" if not missing else f"missing: {missing[:5]}")
    if missing:
        return

    if check_hashes:
        mismatched = [s.path for s in manifest.shards if hasher(dataset_dir / s.path) != s.sha256]
        report.add(
            "shard_sha256",
            not mismatched,
            "ok" if not mismatched else f"mismatch: {mismatched[:5]}",
        )

    shard_problems: list[str] = []
    for shard in manifest.shards:
        path = dataset_dir / shard.path
        try:
            ok, detail = (
                _check_parquet_shard(path, shard.documents)
                if shard.format == "parquet"
                else _check_jsonl_shard(path, shard.documents)
            )
        except (OSError, pa.ArrowException, ValueError) as exc:
            ok, detail = False, f"unreadable: {exc}"
        if not ok:
            shard_problems.append(f"{shard.path}: {detail}")
    report.add(
        "shard_rows_and_schema",
        not shard_problems,
        "ok" if not shard_problems else "; ".join(shard_problems[:5]),
    )

    total_docs = sum(s.documents for s in manifest.shards)
    split_problems = [
        split
        for split, summary in manifest.splits.items()
        if summary.documents != sum(s.documents for s in manifest.shards if s.split == split)
    ]
    totals_ok = total_docs == manifest.totals.documents and not split_problems
    report.add(
        "totals",
        totals_ok,
        "ok" if totals_ok else f"shard sum {total_docs} vs totals {manifest.totals.documents}; splits {split_problems}",
    )

    parquet_paths = [dataset_dir / s.path for s in manifest.shards if s.format == "parquet"]
    jsonl_paths = [dataset_dir / s.path for s in manifest.shards if s.format == "jsonl"]
    if not (parquet_paths or jsonl_paths) or shard_problems:
        return
    # Only constant column lists and escaped shard paths from the manifest are interpolated.
    relations = []
    if parquet_paths:
        relations.append(f"SELECT {_KEY_COLUMNS} FROM read_parquet({_parquet_list(parquet_paths)})")  # noqa: S608
    if jsonl_paths:
        json_reader = (
            f"read_json({_parquet_list(jsonl_paths)}, format='newline_delimited', compression='zstd', "
            f"columns={_JSON_COLUMNS})"
        )
        relations.append(f"SELECT {_KEY_COLUMNS} FROM {json_reader}")  # noqa: S608
    source = "(" + " UNION ALL ".join(relations) + ")"
    connection = _connect(memory_limit_mb, threads)
    try:
        # The relation is built from escaped local shard paths listed in the manifest.
        row = connection.execute(f"SELECT count(*), count(DISTINCT document_id) FROM {source}").fetchone()  # noqa: S608
        if row is None:  # an aggregate always returns one row
            raise DatasetValidationError("DuckDB returned no row for COUNT(*)", ["internal error"])
        duplicates_ids = int(row[0]) - int(row[1])
        report.add(
            "unique_document_ids",
            duplicates_ids == 0,
            "ok" if duplicates_ids == 0 else f"{duplicates_ids} duplicate ids",
        )

        leak_row = connection.execute(_LEAKAGE_SQL.replace("{data}", source)).fetchone()
        if leak_row is None:
            raise DatasetValidationError("DuckDB returned no row for the leakage check", ["internal error"])
        leaks = int(leak_row[0])
        report.add(
            "no_split_leakage",
            leaks == 0,
            "ok" if leaks == 0 else f"{leaks} groups span multiple splits",
        )
    finally:
        connection.close()

    pairs = _sample_text_hashes(parquet_paths[0]) if parquet_paths else _sample_jsonl_hashes(jsonl_paths[0])
    checked = len(pairs)
    bad = sum(content_hash(text) != digest for text, digest in pairs)
    report.add(
        "content_hash_spot_check",
        bad == 0,
        f"{checked} rows checked" if bad == 0 else f"{bad}/{checked} mismatched",
    )
