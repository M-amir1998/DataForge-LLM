"""Load, resolve, and fingerprint configuration files.

Rules:

* YAML is parsed with ``safe_load`` only.
* Each file may declare ``base_dir`` (relative to the file); every relative path inside the
  file resolves against it. This keeps configs independent of the current directory.
* A stage section given as a string is an include: the referenced YAML file becomes the
  stage configuration (e.g. ``quality: configs/quality/web.yaml``).
* The **config hash** covers only output-affecting settings (sources, stages, sharding,
  tokenizer, stage order). Execution and storage knobs are excluded, so changing a batch size
  or a work directory never creates a new dataset version.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from dataforge.config.models import PipelineConfig
from dataforge.config.sources import SourceConfig, SourceRegistryConfig
from dataforge.domain.hashing import fingerprint
from dataforge.errors import ConfigError

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """A source configuration together with the directory its paths resolve against."""

    source_id: str
    config: SourceConfig
    base_dir: Path

    def resolve(self, relative: str) -> Path:
        return resolve_path(self.base_dir, relative)


@dataclass(frozen=True, slots=True)
class LoadedPipelineConfig:
    """A validated pipeline configuration with resolved sources and its content hash."""

    config: PipelineConfig
    path: Path
    base_dir: Path
    sources: tuple[ResolvedSource, ...]
    config_hash: str

    def resolve(self, relative: str) -> Path:
        return resolve_path(self.base_dir, relative)

    def source(self, source_id: str) -> ResolvedSource:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise ConfigError(f"unknown source '{source_id}'")

    def hash_view(self) -> dict[str, Any]:
        return output_affecting_view(self.config, self.sources)


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML mapping with ``safe_load``; raise :class:`ConfigError` on any problem."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def format_validation_error(path: Path, error: ValidationError) -> str:
    lines = [f"{path}: invalid configuration ({error.error_count()} problem(s))"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def validate_model(model: type[ModelT], data: Mapping[str, Any], path: Path) -> ModelT:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(path, exc)) from exc


def _file_base_dir(path: Path, data: Mapping[str, Any]) -> Path:
    declared = data.get("base_dir", ".")
    if not isinstance(declared, str):
        raise ConfigError(f"{path}: base_dir must be a string")
    return resolve_path(path.parent.resolve(), declared)


def load_source_registry(path: Path) -> tuple[SourceRegistryConfig, Path]:
    """Load a source registry file; returns the model and its resolved base directory."""
    path = path.resolve()
    data = load_yaml(path)
    registry = validate_model(SourceRegistryConfig, data, path)
    return registry, _file_base_dir(path, data)


def _expand_stage_includes(data: dict[str, Any], base_dir: Path, path: Path) -> None:
    stages = data.get("stages")
    if stages is None:
        return
    if not isinstance(stages, dict):
        raise ConfigError(f"{path}: 'stages' must be a mapping")
    for name, value in list(stages.items()):
        if isinstance(value, str):
            include_path = resolve_path(base_dir, value)
            stages[name] = load_yaml(include_path)


def load_pipeline_config(path: Path) -> LoadedPipelineConfig:
    """Load a pipeline config, expand includes, resolve sources, and compute its hash."""
    path = path.resolve()
    data = load_yaml(path)
    base_dir = _file_base_dir(path, data)
    _expand_stage_includes(data, base_dir, path)
    config = validate_model(PipelineConfig, data, path)

    resolved: dict[str, ResolvedSource] = {}
    if config.sources.registry is not None:
        registry, registry_base = load_source_registry(resolve_path(base_dir, config.sources.registry))
        for source_id, source in registry.sources.items():
            resolved[source_id] = ResolvedSource(source_id, source, registry_base)
    for source_id, source in config.sources.inline.items():
        if source_id in resolved:
            raise ConfigError(f"{path}: inline source '{source_id}' shadows a registry source")
        resolved[source_id] = ResolvedSource(source_id, source, base_dir)

    if config.sources.include:
        missing = [sid for sid in config.sources.include if sid not in resolved]
        if missing:
            raise ConfigError(f"{path}: sources.include references unknown sources: {missing}")
        selected = tuple(resolved[sid] for sid in config.sources.include)
    else:
        selected = tuple(resolved.values())
    if not selected:
        raise ConfigError(f"{path}: no sources selected")

    config_hash = fingerprint(output_affecting_view(config, selected))
    return LoadedPipelineConfig(config=config, path=path, base_dir=base_dir, sources=selected, config_hash=config_hash)


def output_affecting_view(config: PipelineConfig, sources: tuple[ResolvedSource, ...]) -> dict[str, Any]:
    """The canonical subset of configuration that determines pipeline output."""
    return {
        "version": config.version,
        "pipeline": {"name": config.pipeline.name, "stage_order": list(config.stage_order())},
        "sources": [
            {
                "source_id": source.source_id,
                **source.config.model_dump(mode="json", exclude={"description"}),
            }
            for source in sources
        ],
        "tokenizer": config.tokenizer.model_dump(mode="json"),
        "stages": config.stages.model_dump(mode="json"),
        "shard": config.shard.model_dump(mode="json"),
    }
