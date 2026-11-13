"""Domain model: canonical documents, identity, and hashing. Free of I/O and frameworks."""

from dataforge.domain.document import (
    DOCUMENT_SCHEMA_VERSION,
    UNKNOWN_LICENSE,
    ContaminationHit,
    ContentKind,
    Document,
    DocumentMetadata,
    Provenance,
)
from dataforge.domain.hashing import content_hash, stable_document_id

__all__ = [
    "DOCUMENT_SCHEMA_VERSION",
    "UNKNOWN_LICENSE",
    "ContaminationHit",
    "ContentKind",
    "Document",
    "DocumentMetadata",
    "Provenance",
    "content_hash",
    "stable_document_id",
]
