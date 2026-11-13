"""Streaming ingestion: sources -> parsed, identified, provenance-stamped documents.

The *cursor* counts raw items (records and record errors) across all sources in registry
order. Checkpoints store it, and resume skips exactly that many items, so ingestion
restarts mid-source without re-emitting anything.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import SourceBase
from dataforge.domain.document import ContentKind, Document, DocumentMetadata, Provenance
from dataforge.domain.hashing import content_hash, stable_document_id
from dataforge.errors import SourceFormatError
from dataforge.licensing.spdx import normalize_license
from dataforge.parsing.base import ParserRegistry
from dataforge.sources.base import RawRecord, RecordError, SourceReader


@dataclass(slots=True)
class InputBatch:
    documents: list[Document]
    cursor: int
    counters: Counter[str] = field(default_factory=Counter)
    artifacts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def record_to_document(record: RawRecord, source: ResolvedSource, parsers: ParserRegistry) -> Document:
    config: SourceBase = source.config
    parsed = parsers.parse(record.content, record.mime_type)
    is_code = (
        source.config.type == "code"
        or config.content_kind == "code"
        or (config.content_kind == "auto" and record.programming_language is not None)
    )
    if record.license is not None:
        license_id = normalize_license(record.license)
        license_source = f"record field '{config.license_field}'"
    else:
        license_id = normalize_license(config.license)
        license_source = config.license_source or "source registry"
    metadata = DocumentMetadata(
        content_kind=ContentKind.CODE if is_code else ContentKind.TEXT,
        programming_language=record.programming_language,
        license=license_id,
        license_source=license_source,
        usage_policy=config.usage_policy,
        url=record.url,
        title=parsed.title or record.title,
        timestamp=record.timestamp,
        content_hash=content_hash(parsed.text),
        extra=dict(record.extra),
    )
    if record.decode_replaced:
        metadata.flags.append("ingest:decode_replaced")
    provenance = Provenance(
        source_id=record.source_id,
        source_uri=record.source_uri,
        record_locator=record.locator,
        record_id=record.record_id,
        source_version=config.version,
        source_fingerprint=record.source_fingerprint,
        transformations=[parsed.transformation] if parsed.transformation else [],
    )
    return Document(
        document_id=stable_document_id(record.source_id, record.record_id or record.locator, record.content),
        source_id=record.source_id,
        text=parsed.text,
        language=config.language_hint,
        mime_type=record.mime_type,
        metadata=metadata,
        provenance=provenance,
    )


class Ingestor:
    """Iterates all sources in order and yields document batches with a resumable cursor."""

    def __init__(self, readers: Sequence[SourceReader], parsers: ParserRegistry | None = None) -> None:
        self.readers = list(readers)
        self.parsers = parsers or ParserRegistry()

    def _items(self) -> Iterator[tuple[SourceReader, RawRecord | RecordError]]:
        for reader in self.readers:
            for item in reader.iter_records():
                yield reader, item

    def iter_batches(self, batch_size: int, skip: int = 0) -> Iterator[InputBatch]:
        cursor = 0
        errors_per_source: Counter[str] = Counter()
        batch = InputBatch([], 0)
        for reader, item in self._items():
            cursor += 1
            config = reader.config
            if isinstance(item, RecordError):
                errors_per_source[reader.source_id] += 1
                too_many = errors_per_source[reader.source_id] > config.max_record_errors
                if cursor <= skip:
                    continue
                if config.on_record_error == "fail" or too_many:
                    raise SourceFormatError(
                        f"source '{reader.source_id}': record error '{item.error_type}' at {item.locator}"
                        + (f" (more than {config.max_record_errors} errors)" if too_many else "")
                    )
                batch.counters[f"errors|{reader.source_id}|{item.error_type}"] += 1
                batch.artifacts.setdefault("ingest_errors", []).append(
                    {
                        "source_id": item.source_id,
                        "locator": item.locator,
                        "error_type": item.error_type,
                        "message": item.message[:200],
                    }
                )
                continue
            if cursor <= skip:
                continue
            document = record_to_document(item, reader.source, self.parsers)
            batch.documents.append(document)
            batch.counters[f"documents|{reader.source_id}"] += 1
            batch.counters[f"bytes|{reader.source_id}"] += document.num_bytes
            if len(batch.documents) >= batch_size:
                batch.cursor = cursor
                yield batch
                batch = InputBatch([], 0)
        batch.cursor = cursor
        if batch.documents or batch.counters:
            yield batch
