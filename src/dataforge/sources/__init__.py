"""Sources: registry, readers, and safe file/archive access."""

from dataforge.sources.base import RawRecord, RecordError, SourceDescription, SourceReader
from dataforge.sources.registry import SourceRegistry, build_reader

__all__ = [
    "RawRecord",
    "RecordError",
    "SourceDescription",
    "SourceReader",
    "SourceRegistry",
    "build_reader",
]
