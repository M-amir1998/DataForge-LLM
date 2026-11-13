"""Benchmark registry: which evaluation sets to protect, and where their local copies live.

Benchmarks are never downloaded automatically. Each entry records its license and origin;
entries marked ``required: false`` are skipped with a note when the local file is absent
(e.g. a licensed set the operator has not fetched).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import Field

from dataforge.config.loader import load_yaml, resolve_path, validate_model
from dataforge.config.sources import StrictModel
from dataforge.dedup.shingles import ngram_hashes, normalize_for_matching, tokenize, word_hashes
from dataforge.domain.document import UNKNOWN_LICENSE
from dataforge.domain.hashing import sha256_bytes, sha256_file
from dataforge.errors import ConfigError, SourceFormatError, SourceNotFound


class BenchmarkSpec(StrictModel):
    path: str
    format: Literal["jsonl"] = "jsonl"
    fields: tuple[str, ...] = Field(min_length=1)
    id_field: str | None = "id"
    license: str = UNKNOWN_LICENSE
    source_url: str | None = None
    description: str = ""
    required: bool = True


class BenchmarkRegistryConfig(StrictModel):
    version: Literal[1] = 1
    base_dir: str = "."
    benchmarks: dict[str, BenchmarkSpec]


@dataclass(frozen=True, slots=True)
class Probe:
    """One protected text field of one benchmark item, in normalized and hashed form."""

    benchmark: str
    item_id: str
    field: str
    normalized: str
    normalized_hash: str
    ngrams: npt.NDArray[np.uint64]

    @property
    def evidence_hash(self) -> str:
        """Short, non-reversible identifier for reports (never the benchmark text)."""
        return self.normalized_hash[:16]


@dataclass(frozen=True, slots=True)
class BenchmarkInfo:
    name: str
    path: str
    license: str
    source_url: str | None
    items: int
    probes: int
    short_probes: int
    sha256: str | None
    available: bool


def _probe(benchmark: str, item_id: str, field: str, text: str, ngram_size: int) -> Probe:
    normalized = normalize_for_matching(text)
    grams = np.unique(ngram_hashes(word_hashes(tokenize(text)), ngram_size, short_as_single=False))
    return Probe(benchmark, item_id, field, normalized, sha256_bytes(normalized.encode("utf-8")), grams)


def load_benchmarks(
    registry_path: Path, include: tuple[str, ...], ngram_size: int
) -> tuple[list[Probe], list[BenchmarkInfo]]:
    """Load probes for the selected benchmarks (all available ones when ``include`` is empty)."""
    data = load_yaml(registry_path)
    registry = validate_model(BenchmarkRegistryConfig, data, registry_path)
    base_dir = resolve_path(registry_path.parent.resolve(), registry.base_dir)
    names = include or tuple(registry.benchmarks)
    unknown = [name for name in names if name not in registry.benchmarks]
    if unknown:
        raise ConfigError(f"{registry_path}: unknown benchmarks {unknown}")

    probes: list[Probe] = []
    infos: list[BenchmarkInfo] = []
    for name in names:
        spec = registry.benchmarks[name]
        path = resolve_path(base_dir, spec.path)
        if not path.is_file():
            if spec.required:
                raise SourceNotFound(f"benchmark '{name}' not found at {spec.path}")
            infos.append(BenchmarkInfo(name, spec.path, spec.license, spec.source_url, 0, 0, 0, None, False))
            continue
        items = probes_before = short = 0
        probes_before = len(probes)
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SourceFormatError(f"benchmark '{name}' line {line_number}: invalid JSON") from exc
                items += 1
                item_id = (
                    str(row.get(spec.id_field))
                    if spec.id_field and row.get(spec.id_field) is not None
                    else f"L{line_number}"
                )
                for field in spec.fields:
                    value = row.get(field)
                    if not isinstance(value, str) or not value.strip():
                        continue
                    probe = _probe(name, item_id, field, value, ngram_size)
                    if probe.ngrams.shape[0] == 0:
                        short += 1
                    probes.append(probe)
        infos.append(
            BenchmarkInfo(
                name,
                spec.path,
                spec.license,
                spec.source_url,
                items,
                len(probes) - probes_before,
                short,
                sha256_file(path),
                True,
            )
        )
    return probes, infos
