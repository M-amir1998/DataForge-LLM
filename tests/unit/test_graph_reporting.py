from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dataforge.config.models import PipelineConfig
from dataforge.errors import ConfigError
from dataforge.pipelines.graph import build_plan
from dataforge.pipelines.stage import StageStats
from dataforge.reporting import svg
from dataforge.reporting.tables import fmt_bytes, fmt_pct, md_table
from dataforge.synthetic.benchmarks import demo_code_items, demo_math_items
from dataforge.telemetry.logging import JsonFormatter
from dataforge.telemetry.metrics import MemorySampler, os_peak_rss_bytes, rss_bytes
from tests.support import REPO_ROOT


def config(**stages: object) -> PipelineConfig:
    return PipelineConfig.model_validate(
        {"pipeline": {"name": "t"}, "sources": {"inline": {"s": {"type": "local", "path": "x"}}}, "stages": stages}
    )


# --------------------------------------------------------------------------- graph


def test_default_plan_segments_at_barriers() -> None:
    plan = build_plan(config())
    labels = [s.label() for s in plan.segments]
    assert labels[0] == "ingest -> normalize -> language -> observe:exact_dedup"
    assert labels[1] == "apply:exact_dedup -> observe:near_dedup"
    assert labels[-1].startswith("apply:near_dedup -> pii -> secrets -> license -> quality -> classify")
    assert plan.materializations == 3
    assert "contamination" in plan.disabled
    assert plan.produces_dataset


def test_mixture_and_time_split_are_barriers() -> None:
    plan = build_plan(
        config(
            mixture={"enabled": True, "weights": {"general": 1.0}},
            split={"strategy": "time", "time_cutoffs": {"test": "2024-01-01"}, "ratios": {"train": 0.9, "test": 0.1}},
        )
    )
    kinds = {n.name: n.kind for n in plan.nodes}
    assert kinds["mixture"] == kinds["split"] == "barrier"
    assert plan.segments[-1].apply == "split"


def test_until_and_ingest_only() -> None:
    plan = build_plan(config(), "exact_dedup")
    assert [s.label() for s in plan.segments] == ["ingest -> normalize -> language -> observe:exact_dedup"]
    assert not plan.produces_dataset
    plan = build_plan(config(), "language")
    assert plan.segments[-1].streaming == ("normalize", "language")
    ingest = build_plan(config(), ingest_only=True)
    assert ingest.segments[0].label() == "ingest"
    with pytest.raises(ConfigError, match="not an enabled stage"):
        build_plan(config(), "contamination")


def test_dependency_validation() -> None:
    with pytest.raises(ConfigError, match="requires 'quality' to run before"):
        build_plan(config(near_dedup={"policy": "keep_highest_quality"}))
    reordered = PipelineConfig.model_validate(
        {
            "pipeline": {
                "name": "t",
                "stage_order": [
                    "normalize",
                    "language",
                    "quality",
                    "exact_dedup",
                    "near_dedup",
                    "pii",
                    "secrets",
                    "license",
                    "classify",
                    "split",
                    "analyze",
                ],
            },
            "sources": {"inline": {"s": {"type": "local", "path": "x"}}},
            "stages": {"near_dedup": {"policy": "keep_highest_quality"}},
        }
    )
    assert [n.name for n in build_plan(reordered).nodes][2] == "quality"
    with pytest.raises(ConfigError, match="classify"):
        build_plan(config(mixture={"enabled": True, "weights": {"general": 1.0}}, classify={"enabled": False}))
    omitted = PipelineConfig.model_validate(
        {
            "pipeline": {"name": "t", "stage_order": ["normalize"]},
            "sources": {"inline": {"s": {"type": "local", "path": "x"}}},
        }
    )
    with pytest.raises(ConfigError, match="omits enabled stages"):
        build_plan(omitted)


def test_stage_stats_merge_and_serialization() -> None:
    a = StageStats("quality", documents_in=10, documents_out=8)
    a.rejected["quality:length"] = 2
    b = StageStats.from_dict(a.as_dict())
    b.merge(a)
    assert (b.documents_in, b.rejected["quality:length"]) == (20, 4)


# --------------------------------------------------------------------------- reporting


def test_svg_charts_are_well_formed_and_escaped() -> None:
    bars = [svg.Bar("a<b>&c", 10, "10"), svg.Bar("zero", 0, "0")]
    for content in (
        svg.horizontal_bars("Title & more", "Sub", bars),
        svg.column_histogram("Hist", "Sub", [("<=1", 3), (">1", 0)], x_label="x"),
        svg.grouped_bars("Mix", "Sub", ["d1", "d2"], [("target", [0.5, 0.5]), ("achieved", [0.4, 0.6])]),
    ):
        root = ET.fromstring(content)
        assert root.tag.endswith("svg")
        assert "prefers-color-scheme: dark" in content
    with pytest.raises(ValueError, match="exactly two"):
        svg.grouped_bars("x", "y", ["a"], [("only", [1.0])])


def test_number_formatting() -> None:
    assert svg.compact(1284) == "1,284"
    assert svg.compact(12_900) == "12.9K"
    assert svg.compact(4_200_000) == "4.2M"
    assert svg.nice_ticks(1501)[-1] >= 1501
    assert svg.nice_ticks(0) == [0.0, 1.0]
    assert fmt_bytes(2048) == "2.00 KB"
    assert fmt_pct(1, 3) == "33.3%"
    assert fmt_pct(1, 0) == "0.0%"
    table = md_table(["A", "B"], [["x|y", 1]])
    assert "x\\|y" in table
    assert table.splitlines()[1] == "| :--- | ---: |"


def test_committed_demo_benchmarks_match_generator() -> None:
    custom = REPO_ROOT / "benchmarks" / "contamination" / "custom"
    math_rows = [json.loads(line) for line in (custom / "demo_math.jsonl").read_text(encoding="utf-8").splitlines()]
    code_rows = [json.loads(line) for line in (custom / "demo_code.jsonl").read_text(encoding="utf-8").splitlines()]
    assert math_rows == demo_math_items()
    assert code_rows == demo_code_items()


# --------------------------------------------------------------------------- telemetry


def test_json_formatter_includes_context_fields() -> None:
    record = logging.LogRecord("dataforge.x", logging.INFO, __file__, 1, "checkpoint", None, None)
    record.run_id = "r1"
    record.documents = 5
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "checkpoint"
    assert payload["run_id"] == "r1"
    assert payload["documents"] == 5
    assert payload["level"] == "info"


def test_memory_metrics() -> None:
    sampler = MemorySampler()
    current = sampler.sample()
    assert current == sampler.peak_bytes > 0
    assert rss_bytes() > 0
    peak = os_peak_rss_bytes()
    assert peak is None or peak >= current // 2


def test_repository_has_no_github_actions_workflows() -> None:
    assert not (REPO_ROOT / ".github" / "workflows").exists(), "CI is Jenkins-only (see ADR-005)"


def test_report_context_type_is_importable(tmp_path: Path) -> None:
    from dataforge.reporting.report import iter_artifact

    assert list(iter_artifact([tmp_path], "rejections")) == []
