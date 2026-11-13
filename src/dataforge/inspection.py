"""Dataset-level services behind ``dataforge dataset|quality|privacy`` commands.

All functions stream documents in batches; none loads a dataset into memory.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import duckdb

from dataforge.config.models import PIIConfig, QualityConfig, SecretsConfig
from dataforge.domain.document import Document
from dataforge.errors import DataForgeError, InvalidManifest
from dataforge.privacy.pii import PIIScanner
from dataforge.privacy.secrets import build_secret_scanner
from dataforge.provenance.manifest import DatasetManifest, read_manifest
from dataforge.quality.scorer import QualityScorer
from dataforge.reporting.analysis import DatasetAnalytics, relation_sql
from dataforge.storage.parts import iter_documents
from dataforge.storage.schema import row_to_document
from dataforge.validation.dataset import iter_zstd_lines

_MAX_SQL_ROWS: Final = 1000


class UnsafeQueryError(DataForgeError):
    """A SQL statement other than a single read-only SELECT was submitted."""

    exit_code = 2


def resolve_dataset(path: Path) -> tuple[Path, DatasetManifest | None]:
    """Accept a dataset directory, its ``manifest.json``, or a directory/file of Parquet parts."""
    if path.name == "manifest.json":
        return path.parent, read_manifest(path)
    if path.is_dir() and (path / "manifest.json").is_file():
        return path, read_manifest(path / "manifest.json")
    if path.exists():
        return path, None
    raise InvalidManifest(f"no dataset found at {path}")


def _parquet_files(root: Path, manifest: DatasetManifest | None) -> list[Path]:
    if manifest is not None:
        return [root / s.path for s in manifest.shards if s.format == "parquet"]
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.parquet"))


def iter_dataset(path: Path, batch_size: int = 256) -> Iterator[list[Document]]:
    root, manifest = resolve_dataset(path)
    yield from iter_documents(_parquet_files(root, manifest), batch_size)
    if manifest is not None:
        for shard in manifest.shards:
            if shard.format != "jsonl":
                continue
            batch: list[Document] = []
            for line in iter_zstd_lines(root / shard.path):
                if line.strip():
                    batch.append(row_to_document(json.loads(line)))
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
            if batch:
                yield batch


# --------------------------------------------------------------------------- quality


@dataclass(slots=True)
class QualityAnalysis:
    documents: int = 0
    passed: int = 0
    evaluated: Counter[str] = field(default_factory=Counter)
    failed: Counter[str] = field(default_factory=Counter)
    score_bins: Counter[int] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "passed": self.passed,
            "signals": {
                name: {"evaluated": self.evaluated[name], "failed": self.failed[name]}
                for name in sorted(self.evaluated)
            },
            "score_histogram": {f"{b / 10:.1f}": self.score_bins[b] for b in range(10)},
        }


def analyze_quality(path: Path, config: QualityConfig | None = None) -> QualityAnalysis:
    scorer = QualityScorer(config or QualityConfig())
    result = QualityAnalysis()
    for batch in iter_dataset(path):
        for assessment in scorer.assess_batch(batch):
            result.documents += 1
            result.passed += assessment.passed
            result.score_bins[min(9, int(assessment.score * 10))] += 1
            for signal in assessment.signals:
                if signal.applicable:
                    result.evaluated[signal.name] += 1
                    result.failed[signal.name] += not signal.passed
    return result


# --------------------------------------------------------------------------- privacy


@dataclass(slots=True)
class PrivacyScan:
    documents: int = 0
    documents_with_pii: int = 0
    documents_with_secrets: int = 0
    detections: Counter[str] = field(default_factory=Counter)
    documents_by_type: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "documents_with_pii": self.documents_with_pii,
            "documents_with_secrets": self.documents_with_secrets,
            "detections": dict(sorted(self.detections.items())),
            "documents_by_type": dict(sorted(self.documents_by_type.items())),
        }


def scan_privacy(path: Path, pii: PIIConfig | None = None, secrets: SecretsConfig | None = None) -> PrivacyScan:
    """Count PII and secret detections by type. Never returns matched values."""
    pii = pii or PIIConfig()
    secrets = secrets or SecretsConfig()
    pii_scanner = PIIScanner(pii.detectors)
    secret_scanner = build_secret_scanner(secrets.detectors, secrets.min_entropy, secrets.min_length)
    result = PrivacyScan()
    for batch in iter_dataset(path):
        for document in batch:
            result.documents += 1
            pii_spans = pii_scanner.scan(document.text)
            secret_spans = secret_scanner.scan(document.text)
            result.documents_with_pii += bool(pii_spans)
            result.documents_with_secrets += bool(secret_spans)
            kinds = Counter(span.kind for span in [*pii_spans, *secret_spans])
            result.detections.update(kinds)
            result.documents_by_type.update(kinds.keys())
    return result


# --------------------------------------------------------------------------- inspection / SQL


def inspect_dataset(path: Path, memory_limit_mb: int = 512) -> dict[str, Any]:
    root, manifest = resolve_dataset(path)
    if manifest is None:
        raise InvalidManifest(f"{path} has no manifest.json; inspect works on DataForge datasets")
    with DatasetAnalytics(root, manifest, memory_limit_mb=memory_limit_mb) as analytics:
        duplicates = analytics.query(
            "SELECT count(*) FILTER (WHERE exact_dup_group IS NOT NULL), "
            "count(*) FILTER (WHERE near_dup_cluster IS NOT NULL), count(DISTINCT near_dup_cluster) FROM {data}"
        )
        exact_members, near_members, clusters = duplicates[0] if duplicates else (0, 0, 0)
        return {
            "dataset_id": manifest.dataset_id,
            "created_at": manifest.created_at,
            "documents": manifest.totals.documents,
            "text_bytes": manifest.totals.text_bytes,
            "shard_bytes": manifest.totals.bytes,
            "estimated_tokens": manifest.totals.tokens,
            "splits": {name: s.documents for name, s in manifest.splits.items()},
            "sources": analytics.grouped("source_id"),
            "licenses": analytics.grouped("license"),
            "languages": analytics.grouped(
                "language",
                missing="CASE WHEN content_kind = 'code' THEN 'n/a (code)' ELSE 'undetermined' END",
            ),
            "domains": analytics.grouped("domain"),
            "quality_histogram": analytics.quality_histogram(),
            "length": analytics.length_stats(),
            "duplicates": {
                "documents_with_exact_group": int(exact_members or 0),
                "documents_in_near_clusters": int(near_members or 0),
                "near_clusters": int(clusters or 0),
            },
        }


def run_sql(
    path: Path, query: str, *, max_rows: int = 100, memory_limit_mb: int = 512
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run one read-only SELECT against the dataset, exposed as the view ``docs``.

    Only a single SELECT statement is accepted (no COPY, ATTACH, INSTALL, or writes) and
    extension auto-install/auto-load is disabled, so queries stay local and read-only.
    """
    root, manifest = resolve_dataset(path)
    relation = (
        relation_sql(root, manifest)
        if manifest is not None
        else (
            "read_parquet(["
            + ", ".join("'" + p.as_posix().replace("'", "''") + "'" for p in _parquet_files(root, None))
            + "])"
        )
    )
    if relation is None:
        raise InvalidManifest(f"{path} has no shards")
    statements = duckdb.extract_statements(query)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise UnsafeQueryError("only a single read-only SELECT statement is allowed")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute(f"SET memory_limit = '{int(memory_limit_mb)}MB'")
        # ``relation`` lists escaped local shard paths; the user query itself is checked above.
        connection.execute(f"CREATE VIEW docs AS SELECT * FROM {relation}")  # noqa: S608
        cursor = connection.execute(query)
        columns = [description[0] for description in cursor.description or []]
        rows = cursor.fetchmany(min(max_rows, _MAX_SQL_ROWS))
        return columns, rows
    finally:
        connection.close()
