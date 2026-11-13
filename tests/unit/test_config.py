from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from dataforge.config.loader import load_pipeline_config, load_source_registry
from dataforge.config.models import MixtureConfig, NearDedupConfig, SplitConfig, TokenizerConfig
from dataforge.errors import ConfigError
from tests.support import REPO_ROOT


def _write(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return path


def _minimal(tmp_path: Path, **extra: object) -> dict[str, object]:
    (tmp_path / "docs.jsonl").write_text('{"text": "hello"}\n', encoding="utf-8")
    config: dict[str, object] = {
        "pipeline": {"name": "unit"},
        "sources": {"inline": {"docs": {"type": "local", "path": "docs.jsonl", "license": "MIT"}}},
    }
    config.update(extra)
    return config


def test_repository_demo_and_smoke_configs_load() -> None:
    for name in ("demo.yaml", "smoke.yaml"):
        loaded = load_pipeline_config(REPO_ROOT / "configs" / "pipelines" / name)
        assert loaded.sources
        assert len(loaded.config_hash) == 64


def test_unknown_keys_fail_early_with_location(tmp_path: Path) -> None:
    config = _minimal(tmp_path, stages={"quality": {"enabled": True, "min_char": 10}})
    with pytest.raises(ConfigError, match=r"stages\.quality\.min_char"):
        load_pipeline_config(_write(tmp_path, "p.yaml", config))


def test_invalid_yaml_and_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_pipeline_config(_write(tmp_path, "bad.yaml", "pipeline: [unclosed"))
    with pytest.raises(ConfigError, match="not found"):
        load_pipeline_config(tmp_path / "missing.yaml")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_pipeline_config(_write(tmp_path, "list.yaml", "- a\n- b\n"))


def test_relative_paths_resolve_against_base_dir(tmp_path: Path) -> None:
    sub = tmp_path / "configs"
    sub.mkdir()
    config = _minimal(tmp_path, base_dir="..")
    loaded = load_pipeline_config(_write(sub, "p.yaml", config))
    assert loaded.base_dir == tmp_path.resolve()
    assert loaded.sources[0].resolve("docs.jsonl") == (tmp_path / "docs.jsonl").resolve()


def test_stage_include_loads_preset(tmp_path: Path) -> None:
    _write(tmp_path, "quality.yaml", {"combine": "min_score", "min_score": 0.6})
    loaded = load_pipeline_config(_write(tmp_path, "p.yaml", _minimal(tmp_path, stages={"quality": "quality.yaml"})))
    assert loaded.config.stages.quality.combine == "min_score"
    assert loaded.config.stages.quality.min_score == 0.6


def test_config_hash_ignores_execution_and_storage_but_not_stages(tmp_path: Path) -> None:
    base = _minimal(tmp_path)
    h1 = load_pipeline_config(_write(tmp_path, "a.yaml", base)).config_hash
    h2 = load_pipeline_config(
        _write(tmp_path, "b.yaml", {**base, "execution": {"batch_size": 7}, "storage": {"work_dir": "elsewhere"}})
    ).config_hash
    h3 = load_pipeline_config(
        _write(tmp_path, "c.yaml", {**base, "stages": {"quality": {"enabled": False}}})
    ).config_hash
    assert h1 == h2
    assert h1 != h3


def test_registry_and_inline_sources(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    _write(tmp_path, "reg.yaml", {"sources": {"docs": {"type": "local", "path": "a.txt"}}})
    config = _minimal(tmp_path)
    config["sources"] = {"registry": "reg.yaml", "inline": {"docs": {"type": "local", "path": "a.txt"}}}
    with pytest.raises(ConfigError, match="shadows"):
        load_pipeline_config(_write(tmp_path, "p.yaml", config))
    config["sources"] = {"registry": "reg.yaml", "include": ["nope"]}
    with pytest.raises(ConfigError, match="unknown sources"):
        load_pipeline_config(_write(tmp_path, "p2.yaml", config))
    registry, base = load_source_registry(tmp_path / "reg.yaml")
    assert "docs" in registry.sources
    assert base == tmp_path.resolve()


def test_source_ids_are_validated(tmp_path: Path) -> None:
    config = _minimal(tmp_path)
    config["sources"] = {"inline": {"Bad Id": {"type": "local", "path": "docs.jsonl"}}}
    with pytest.raises(ConfigError):
        load_pipeline_config(_write(tmp_path, "p.yaml", config))


@pytest.mark.parametrize(
    ("model", "payload", "message"),
    [
        (MixtureConfig, {"enabled": True, "weights": {"a": 0.5, "b": 0.4}}, "sum to 1.0"),
        (MixtureConfig, {"enabled": True, "weights": {}}, "must not be empty"),
        (SplitConfig, {"ratios": {"train": 0.9, "test": 0.2}}, "sum to 1.0"),
        (SplitConfig, {"strategy": "time"}, "time_cutoffs"),
        (SplitConfig, {"assignments": {"web": "holdout"}}, "not a configured split"),
        (NearDedupConfig, {"bands": 20}, "both"),
        (NearDedupConfig, {"bands": 20, "rows": 10}, "must not exceed"),
        (TokenizerConfig, {"kind": "hf"}, "tokenizer_path"),
    ],
)
def test_semantic_validation(model: type[BaseModel], payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        model.model_validate(payload)


def test_contamination_requires_registry_when_enabled(tmp_path: Path) -> None:
    config = _minimal(tmp_path, stages={"contamination": {"enabled": True}})
    with pytest.raises(ConfigError, match="registry"):
        load_pipeline_config(_write(tmp_path, "p.yaml", config))


def test_disabled_mixture_does_not_need_weights() -> None:
    assert not MixtureConfig().enabled
