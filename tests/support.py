"""Shared test helpers (importable as ``tests.support``)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from dataforge.domain.document import ContentKind, Document, DocumentMetadata, Provenance
from dataforge.domain.hashing import content_hash, stable_document_id
from dataforge.synthetic.corpus import generate_sample_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_document(
    text: str,
    *,
    source_id: str = "src",
    record_id: str | None = None,
    kind: ContentKind = ContentKind.TEXT,
    license_id: str = "MIT",
    language: str | None = "en",
    quality: float | None = None,
    domain: str | None = None,
    timestamp: str | None = None,
    url: str | None = None,
) -> Document:
    record = record_id or f"r-{uuid.uuid4().hex[:8]}"
    return Document(
        document_id=stable_document_id(source_id, record, text),
        source_id=source_id,
        text=text,
        language=language,
        mime_type="text/plain",
        metadata=DocumentMetadata(
            content_kind=kind,
            license=license_id,
            content_hash=content_hash(text),
            quality_score=quality,
            domain=domain,
            timestamp=timestamp,
            url=url,
        ),
        provenance=Provenance(source_id=source_id, source_uri="test", record_locator=record, record_id=record),
    )


def deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def build_project(root: Path, *, scale: float = 0.1, seed: int = 7) -> Path:
    """Mirror the repository layout (configs + benchmarks) and generate the smoke corpus."""
    shutil.copytree(REPO_ROOT / "configs", root / "configs")
    shutil.copytree(REPO_ROOT / "benchmarks" / "contamination", root / "benchmarks" / "contamination")
    generate_sample_corpus(root / "data" / "smoke", seed=seed, scale=scale)
    return root


def write_pipeline(project: Path, storage_root: Path, overrides: Mapping[str, Any] | None = None) -> Path:
    """Write a pipeline config derived from smoke.yaml with isolated storage directories."""
    base = yaml.safe_load((project / "configs" / "pipelines" / "smoke.yaml").read_text(encoding="utf-8"))
    base["base_dir"] = "."
    base["sources"]["registry"] = "configs/sources/smoke.yaml"
    base["storage"]["work_dir"] = str(storage_root / "work")
    base["storage"]["output_dir"] = str(storage_root / "datasets")
    config = deep_merge(base, overrides or {})
    path = project / f"pipeline-{uuid.uuid4().hex[:10]}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path
