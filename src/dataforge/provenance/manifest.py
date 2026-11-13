"""Dataset manifest: the single source of truth about a dataset version.

A dataset is complete if and only if its manifest exists and validates. The manifest
records what was produced (shards with SHA-256), from what (sources with content
fingerprints, benchmarks), by what (code version, Git SHA, code fingerprint), and how (the
output-affecting configuration, embedded verbatim, plus its hash).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dataforge.errors import InvalidManifest

MANIFEST_SCHEMA_VERSION: Final = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileEntry(_Model):
    path: str
    bytes: int
    sha256: str


class SourceEntry(_Model):
    source_id: str
    type: str
    uri: str
    license: str
    license_field: str | None = None
    license_source: str | None
    usage_policy: str
    version: str | None
    fingerprint: str
    reproducible: bool
    files: list[FileEntry] = Field(default_factory=list)
    documents_read: int = 0
    record_errors: int = 0
    notes: list[str] = Field(default_factory=list)


class BenchmarkEntry(_Model):
    name: str
    path: str
    license: str
    source_url: str | None
    items: int
    probes: int
    sha256: str | None
    available: bool


class ShardEntry(_Model):
    path: str
    split: str
    format: Literal["parquet", "jsonl"]
    compression: str
    documents: int
    bytes: int
    text_bytes: int
    tokens: int
    sha256: str


class SplitSummary(_Model):
    documents: int
    bytes: int
    text_bytes: int
    tokens: int
    shards: int


class StageSummary(_Model):
    name: str
    kind: str
    mode: str
    documents_in: int
    documents_out: int
    bytes_in: int
    bytes_out: int
    rejected: dict[str, int] = Field(default_factory=dict)
    flagged: dict[str, int] = Field(default_factory=dict)
    seconds: float = 0.0


class CodeEntry(_Model):
    dataforge_version: str
    git_sha: str | None
    git_dirty: bool | None
    code_fingerprint: str


class PipelineEntry(_Model):
    name: str
    config_path: str
    config_hash: str
    stage_order: list[str]
    config: dict[str, Any]


class DatasetManifest(_Model):
    schema_version: Literal["1.0"] = MANIFEST_SCHEMA_VERSION
    document_schema_version: str
    dataset_id: str
    name: str
    version: str
    fingerprint: str
    created_at: str
    run_id: str
    code: CodeEntry
    pipeline: PipelineEntry
    tokenizer: str
    sources: list[SourceEntry]
    benchmarks: list[BenchmarkEntry] = Field(default_factory=list)
    totals: SplitSummary
    splits: dict[str, SplitSummary]
    shards: list[ShardEntry]
    stages: list[StageSummary]
    reproduce: dict[str, str]


def write_manifest(path: Path, manifest: DatasetManifest) -> None:
    payload = manifest.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_manifest(path: Path) -> DatasetManifest:
    """Parse and validate a manifest.

    Raises:
        InvalidManifest: missing file, invalid JSON, or schema violation.
    """
    if path.is_dir():
        path = path / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvalidManifest(f"manifest not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidManifest(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise InvalidManifest("manifest must be a JSON object")
    version = payload.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise InvalidManifest(f"unsupported manifest schema_version {version!r} (expected {MANIFEST_SCHEMA_VERSION})")
    try:
        return DatasetManifest.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])
        raise InvalidManifest(f"manifest violates schema: {problems}") from exc
