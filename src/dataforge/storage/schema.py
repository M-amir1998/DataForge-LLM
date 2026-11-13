"""Arrow schema for canonical documents and lossless conversion to and from Arrow.

The schema is versioned through Parquet key-value metadata
(``dataforge.document_schema_version``). Additive columns bump the minor version; renames
or type changes bump the major version and require a documented migration.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Final

import pyarrow as pa

from dataforge.domain.document import (
    DOCUMENT_SCHEMA_VERSION,
    UNKNOWN_LICENSE,
    ContaminationHit,
    ContentKind,
    Document,
    DocumentMetadata,
    Provenance,
)

SCHEMA_VERSION_KEY: Final = b"dataforge.document_schema_version"

_PROVENANCE: Final = pa.struct(
    [
        ("source_uri", pa.string()),
        ("record_locator", pa.string()),
        ("record_id", pa.string()),
        ("source_version", pa.string()),
        ("source_fingerprint", pa.string()),
        ("transformations", pa.list_(pa.string())),
    ]
)
_CONTAMINATION: Final = pa.list_(
    pa.struct(
        [
            ("benchmark", pa.string()),
            ("item_id", pa.string()),
            ("method", pa.string()),
            ("score", pa.float32()),
        ]
    )
)

DOCUMENT_SCHEMA: Final = pa.schema(
    [
        ("document_id", pa.string()),
        ("source_id", pa.string()),
        ("text", pa.large_string()),
        ("language", pa.string()),
        ("language_confidence", pa.float32()),
        ("mime_type", pa.string()),
        ("content_kind", pa.string()),
        ("programming_language", pa.string()),
        ("license", pa.string()),
        ("license_source", pa.string()),
        ("usage_policy", pa.string()),
        ("url", pa.string()),
        ("title", pa.string()),
        ("timestamp", pa.string()),
        ("content_hash", pa.string()),
        ("num_chars", pa.int64()),
        ("num_bytes", pa.int64()),
        ("token_estimate", pa.int64()),
        ("domain", pa.string()),
        ("domain_confidence", pa.float32()),
        ("quality_score", pa.float32()),
        ("quality_signals", pa.map_(pa.string(), pa.float64())),
        ("pii_counts", pa.map_(pa.string(), pa.int32())),
        ("secret_counts", pa.map_(pa.string(), pa.int32())),
        ("exact_dup_group", pa.string()),
        ("near_dup_cluster", pa.string()),
        ("near_dup_similarity", pa.float32()),
        ("contamination", _CONTAMINATION),
        ("flags", pa.list_(pa.string())),
        ("split", pa.string()),
        ("extra", pa.string()),
        ("provenance", _PROVENANCE),
    ],
    metadata={SCHEMA_VERSION_KEY: DOCUMENT_SCHEMA_VERSION.encode("ascii")},
)

COLUMN_NAMES: Final = tuple(DOCUMENT_SCHEMA.names)


def documents_to_table(documents: Sequence[Document]) -> pa.Table:
    """Convert documents to an Arrow table with :data:`DOCUMENT_SCHEMA`."""
    columns: dict[str, list[Any]] = {name: [] for name in COLUMN_NAMES}
    for doc in documents:
        meta = doc.metadata
        prov = doc.provenance
        text_bytes = doc.num_bytes
        columns["document_id"].append(doc.document_id)
        columns["source_id"].append(doc.source_id)
        columns["text"].append(doc.text)
        columns["language"].append(doc.language)
        columns["language_confidence"].append(meta.language_confidence)
        columns["mime_type"].append(doc.mime_type)
        columns["content_kind"].append(meta.content_kind.value)
        columns["programming_language"].append(meta.programming_language)
        columns["license"].append(meta.license)
        columns["license_source"].append(meta.license_source)
        columns["usage_policy"].append(meta.usage_policy)
        columns["url"].append(meta.url)
        columns["title"].append(meta.title)
        columns["timestamp"].append(meta.timestamp)
        columns["content_hash"].append(meta.content_hash)
        columns["num_chars"].append(len(doc.text))
        columns["num_bytes"].append(text_bytes)
        columns["token_estimate"].append(meta.token_estimate)
        columns["domain"].append(meta.domain)
        columns["domain_confidence"].append(meta.domain_confidence)
        columns["quality_score"].append(meta.quality_score)
        columns["quality_signals"].append(list(meta.quality_signals.items()))
        columns["pii_counts"].append(list(meta.pii_counts.items()))
        columns["secret_counts"].append(list(meta.secret_counts.items()))
        columns["exact_dup_group"].append(meta.exact_dup_group)
        columns["near_dup_cluster"].append(meta.near_dup_cluster)
        columns["near_dup_similarity"].append(meta.near_dup_similarity)
        columns["contamination"].append(
            [
                {
                    "benchmark": h.benchmark,
                    "item_id": h.item_id,
                    "method": h.method,
                    "score": h.score,
                }
                for h in meta.contamination
            ]
        )
        columns["flags"].append(list(meta.flags))
        columns["split"].append(meta.split)
        columns["extra"].append(
            json.dumps(meta.extra, ensure_ascii=False, sort_keys=True, allow_nan=False) if meta.extra else None
        )
        columns["provenance"].append(
            {
                "source_uri": prov.source_uri,
                "record_locator": prov.record_locator,
                "record_id": prov.record_id,
                "source_version": prov.source_version,
                "source_fingerprint": prov.source_fingerprint,
                "transformations": list(prov.transformations),
            }
        )
    return pa.table(columns, schema=DOCUMENT_SCHEMA)


def _map(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


def row_to_document(row: dict[str, Any]) -> Document:
    prov = row.get("provenance") or {}
    extra_raw = row.get("extra")
    metadata = DocumentMetadata(
        content_kind=ContentKind(row.get("content_kind") or "text"),
        programming_language=row.get("programming_language"),
        language_confidence=row.get("language_confidence"),
        license=row.get("license") or UNKNOWN_LICENSE,
        license_source=row.get("license_source"),
        usage_policy=row.get("usage_policy"),
        url=row.get("url"),
        title=row.get("title"),
        timestamp=row.get("timestamp"),
        content_hash=row.get("content_hash") or "",
        domain=row.get("domain"),
        domain_confidence=row.get("domain_confidence"),
        quality_score=row.get("quality_score"),
        quality_signals={str(k): float(v) for k, v in _map(row.get("quality_signals")).items()},
        pii_counts={str(k): int(v) for k, v in _map(row.get("pii_counts")).items()},
        secret_counts={str(k): int(v) for k, v in _map(row.get("secret_counts")).items()},
        exact_dup_group=row.get("exact_dup_group"),
        near_dup_cluster=row.get("near_dup_cluster"),
        near_dup_similarity=row.get("near_dup_similarity"),
        contamination=[
            ContaminationHit(h["benchmark"], h["item_id"], h["method"], float(h["score"]))
            for h in row.get("contamination") or []
        ],
        flags=list(row.get("flags") or []),
        token_estimate=row.get("token_estimate"),
        split=row.get("split"),
        extra=json.loads(extra_raw) if extra_raw else {},
    )
    provenance = Provenance(
        source_id=row["source_id"],
        source_uri=prov.get("source_uri") or "",
        record_locator=prov.get("record_locator") or "",
        record_id=prov.get("record_id"),
        source_version=prov.get("source_version"),
        source_fingerprint=prov.get("source_fingerprint"),
        transformations=list(prov.get("transformations") or []),
    )
    return Document(
        document_id=row["document_id"],
        source_id=row["source_id"],
        text=row["text"],
        language=row.get("language"),
        mime_type=row.get("mime_type"),
        metadata=metadata,
        provenance=provenance,
    )


def batch_to_documents(batch: pa.RecordBatch | pa.Table) -> list[Document]:
    return [row_to_document(row) for row in batch.to_pylist()]


def schema_matches(schema: pa.Schema) -> bool:
    """True when ``schema`` has exactly the canonical columns and types (metadata ignored)."""
    return bool(schema.remove_metadata().equals(DOCUMENT_SCHEMA.remove_metadata()))
