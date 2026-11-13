"""Source port: everything a reader must provide, independent of where data lives."""

from __future__ import annotations

import datetime as dt
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import SourceBase, SourceConfig
from dataforge.domain.hashing import combine_fingerprints

MAX_RECORD_BYTES: Final = 64 * 1024 * 1024


@dataclass(slots=True)
class RawRecord:
    """A decoded record before parsing: the unit a reader yields."""

    source_id: str
    source_uri: str
    locator: str
    content: str
    mime_type: str | None = None
    record_id: str | None = None
    url: str | None = None
    title: str | None = None
    timestamp: str | None = None
    license: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    source_fingerprint: str | None = None
    programming_language: str | None = None
    decode_replaced: bool = False


@dataclass(frozen=True, slots=True)
class RecordError:
    """A structured, content-free description of a record that could not be read."""

    source_id: str
    locator: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    relative: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SourceDescription:
    """What a source is, how big it is, and a fingerprint of its exact content."""

    source_id: str
    type: str
    uri: str
    files: tuple[FileFingerprint, ...]
    total_bytes: int | None
    fingerprint: str
    reproducible: bool
    notes: tuple[str, ...] = ()

    @staticmethod
    def fingerprint_files(files: tuple[FileFingerprint, ...]) -> str:
        return combine_fingerprints(f"{f.relative}:{f.size}:{f.sha256}" for f in files)


class SourceReader(ABC):
    """Streams :class:`RawRecord` objects (and :class:`RecordError` markers) in a fixed order."""

    def __init__(self, source: ResolvedSource) -> None:
        self.source = source

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def config(self) -> SourceConfig:
        return self.source.config

    @abstractmethod
    def describe(self) -> SourceDescription:
        """Describe the source and fingerprint its content (may read every byte once)."""

    @abstractmethod
    def iter_records(self) -> Iterator[RawRecord | RecordError]:
        """Yield records in a deterministic order; never load the whole source in memory."""

    def estimated_bytes(self) -> int | None:
        """Cheap size estimate used for disk budgeting (``None`` when unknown)."""
        return None


def json_safe(value: Any) -> Any:
    """Convert arbitrary record values into JSON-serialisable equivalents."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, bytes):
        return None
    return str(value)


def normalize_timestamp(value: Any) -> str | None:
    """Render a timestamp field as an ISO-8601 string without guessing time zones."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value):
            return None
        return dt.datetime.fromtimestamp(float(value), tz=dt.UTC).isoformat()
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def record_from_mapping(
    row: Mapping[str, Any],
    *,
    config: SourceBase,
    source_id: str,
    source_uri: str,
    locator: str,
    mime_type: str | None,
    source_fingerprint: str | None,
) -> RawRecord | RecordError:
    """Map a structured row (JSONL/JSON/CSV/Parquet/HF) onto a :class:`RawRecord`."""
    text = row.get(config.text_field)
    if not isinstance(text, str):
        kind = "missing_text_field" if text is None else "non_string_text_field"
        return RecordError(source_id, locator, kind, f"field '{config.text_field}' is {kind}")
    record_id = _optional_str(row.get(config.id_field)) if config.id_field else None
    extra = {name: json_safe(row.get(name)) for name in config.metadata_fields if name in row}
    return RawRecord(
        source_id=source_id,
        source_uri=source_uri,
        locator=locator,
        content=text,
        mime_type=mime_type,
        record_id=record_id,
        url=_optional_str(row.get(config.url_field)) if config.url_field else None,
        title=_optional_str(row.get(config.title_field)) if config.title_field else None,
        timestamp=normalize_timestamp(row.get(config.timestamp_field)) if config.timestamp_field else None,
        license=_optional_str(row.get(config.license_field)) if config.license_field else None,
        extra=extra,
        source_fingerprint=source_fingerprint,
    )


def parse_json_object(line: str) -> dict[str, Any] | None:
    """Parse a JSON object; return ``None`` when the payload is not an object."""
    value = json.loads(line)
    return value if isinstance(value, dict) else None
