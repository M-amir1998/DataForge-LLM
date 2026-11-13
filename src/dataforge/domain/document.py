"""Canonical document model.

The hot path of the pipeline handles millions of records, so documents are slotted
dataclasses rather than validated models: validation happens once at the edges (sources,
configuration, manifests), and stages mutate the documents they own.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Final

from dataforge.domain.hashing import content_hash

#: Version of the canonical document schema (bump on any breaking column change).
DOCUMENT_SCHEMA_VERSION: Final = "1.0"

#: License marker for documents whose license was not declared. Never inferred.
UNKNOWN_LICENSE: Final = "UNKNOWN"


class ContentKind(enum.StrEnum):
    """Coarse format of a document's content; drives format-aware normalization and scoring."""

    TEXT = "text"
    CODE = "code"
    MIXED = "mixed"


@dataclass(slots=True, frozen=True)
class ContaminationHit:
    """A benchmark item that a document overlaps with. Carries no benchmark text."""

    benchmark: str
    item_id: str
    method: str
    score: float


@dataclass(slots=True)
class Provenance:
    """Where a document came from and what happened to it on the way."""

    source_id: str
    source_uri: str
    record_locator: str
    record_id: str | None = None
    source_version: str | None = None
    source_fingerprint: str | None = None
    transformations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentMetadata:
    """Descriptive metadata and the annotations produced by pipeline stages."""

    content_kind: ContentKind = ContentKind.TEXT
    programming_language: str | None = None
    language_confidence: float | None = None
    license: str = UNKNOWN_LICENSE
    license_source: str | None = None
    usage_policy: str | None = None
    url: str | None = None
    title: str | None = None
    timestamp: str | None = None
    content_hash: str = ""
    domain: str | None = None
    domain_confidence: float | None = None
    quality_score: float | None = None
    quality_signals: dict[str, float] = field(default_factory=dict)
    pii_counts: dict[str, int] = field(default_factory=dict)
    secret_counts: dict[str, int] = field(default_factory=dict)
    exact_dup_group: str | None = None
    near_dup_cluster: str | None = None
    near_dup_similarity: float | None = None
    contamination: list[ContaminationHit] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    token_estimate: int | None = None
    split: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Document:
    """A single training document in canonical form."""

    document_id: str
    source_id: str
    text: str
    language: str | None
    mime_type: str | None
    metadata: DocumentMetadata
    provenance: Provenance

    @property
    def num_chars(self) -> int:
        return len(self.text)

    @property
    def num_bytes(self) -> int:
        return len(self.text.encode("utf-8", "surrogatepass"))

    @property
    def is_code(self) -> bool:
        return self.metadata.content_kind is ContentKind.CODE

    def replace_text(self, new_text: str, transformation: str) -> None:
        """Swap in transformed text, refresh the content hash, and record the change."""
        if new_text == self.text:
            return
        self.text = new_text
        self.metadata.content_hash = content_hash(new_text)
        self.provenance.transformations.append(transformation)

    def group_key(self) -> str:
        """Key that must never be split across train/validation/test partitions.

        Near-duplicate clusters take precedence, then exact-duplicate groups, then the
        content hash (identical texts always share a key), then the document id.
        """
        return (
            self.metadata.near_dup_cluster
            or self.metadata.exact_dup_group
            or self.metadata.content_hash
            or self.document_id
        )
