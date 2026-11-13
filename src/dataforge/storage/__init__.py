"""Storage: document schema, intermediate parts, backends, and disk budgeting."""

from dataforge.storage.backend import FsspecStorage, LocalStorage, StorageBackend, storage_for
from dataforge.storage.budget import DiskEstimate, DiskGuard, disk_free_bytes, estimate
from dataforge.storage.schema import DOCUMENT_SCHEMA, batch_to_documents, documents_to_table

__all__ = [
    "DOCUMENT_SCHEMA",
    "DiskEstimate",
    "DiskGuard",
    "FsspecStorage",
    "LocalStorage",
    "StorageBackend",
    "batch_to_documents",
    "disk_free_bytes",
    "documents_to_table",
    "estimate",
    "storage_for",
]
