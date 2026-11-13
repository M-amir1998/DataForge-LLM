"""Experiment: how much data does each filter remove, and how accurately?

Question: on a representative small corpus, how much is removed by exact deduplication,
near deduplication, quality filtering, PII handling, and contamination filtering - both
sequentially (pipeline order, marginal attribution) and independently (each filter on the
full corpus) - and how well do the filters match the phenomena planted in the corpus?

Method:
1. Generate the synthetic sample corpus (fixed seed) with ground-truth labels.
2. Run A (sequential): the demo pipeline in filter mode, mixture disabled.
3. Run B (independent): every filtering stage in annotate mode, so each filter sees the
   whole corpus and records a flag instead of removing.
4. Join removals, flags, and detections with the ground truth (Polars) and report rates,
   overlaps, recall, and precision.

Metric definitions:
- Duplicates are scored per planted group: a group is deduplicated when at most one member
  survives (either copy may legitimately be the one kept, depending on the policy).
- PII and secret recall only counts planted documents that reached that stage (earlier
  stages may remove them first); precision is per detected document.
- Language-filter precision counts removals of non-English pages and of planted junk
  pages without English prose (keyword spam, lorem ipsum, URL lists) as correct.

Usage: uv run python experiments/removal_attribution/run.py [--scale 2.0] [--seed 7]
Writes results/results.json and results/RESULTS.md next to this file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from dataforge.pipelines.runner import RunOptions, RunResult, run_pipeline
from dataforge.synthetic.corpus import generate_sample_corpus
from dataforge.telemetry.logging import configure_logging

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
FILTERS = ("language", "exact_dedup", "near_dedup", "license", "quality", "contamination")
BEFORE_PRIVACY = ("language", "exact_dedup", "near_dedup")
RAW_PREFIX = "data/sample/raw/"
TRUTH_SCHEMA = {
    "source_id": pl.String,
    "record_key": pl.String,
    "non_english": pl.Boolean,
    "low_quality": pl.Boolean,
    "exact_group": pl.String,
    "near_group": pl.String,
    "near_jaccard": pl.Float64,
    "pii": pl.Boolean,
    "secret": pl.Boolean,
    "contamination": pl.String,
    "bad_license": pl.Boolean,
}


def prepare_project(workdir: Path, scale: float, seed: int) -> Path:
    shutil.copytree(ROOT / "configs", workdir / "configs")
    shutil.copytree(ROOT / "benchmarks" / "contamination", workdir / "benchmarks" / "contamination")
    generate_sample_corpus(workdir / "data" / "sample", seed=seed, scale=scale)
    return workdir


def write_config(project: Path, name: str, *, annotate: bool) -> Path:
    config = yaml.safe_load((project / "configs" / "pipelines" / "demo.yaml").read_text(encoding="utf-8"))
    config["base_dir"] = "."
    config["sources"]["registry"] = "configs/sources/demo.yaml"
    config["pipeline"]["name"] = name
    # Keep intermediates: detection artifacts are read back for the analysis.
    config["storage"] = {
        "work_dir": f".dataforge/{name}/work",
        "output_dir": f".dataforge/{name}/datasets",
        "reserve_free_gb": 1,
        "cleanup": "never",
    }
    config["stages"]["mixture"] = {"enabled": False}
    if annotate:
        for stage in FILTERS:
            current = config["stages"].get(stage) or {}
            if isinstance(current, str):  # a preset include such as configs/quality/default.yaml
                current = yaml.safe_load((project / current).read_text(encoding="utf-8"))
            config["stages"][stage] = {**current, "mode": "annotate"}
    path = project / f"{name}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def ground_truth(project: Path) -> pl.DataFrame:
    path = project / "data" / "sample" / "ground_truth.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    originals = {r["exact_duplicate_of"] for r in rows if r["exact_duplicate_of"]}
    records = []
    for r in rows:
        key = f"{r['source_id']}:{r['record_id']}"
        exact_group = r["exact_duplicate_of"] or (key if key in originals else None)
        records.append(
            {
                "source_id": r["source_id"],
                "record_key": r["record_id"],
                "non_english": r["language"] not in ("en", "code"),
                "low_quality": r["low_quality"] is not None,
                "exact_group": exact_group,
                "near_group": r["near_duplicate_group"],
                "near_jaccard": r["near_duplicate_jaccard"],
                "pii": bool(r["pii"]),
                "secret": bool(r["secrets"]),
                "contamination": r["contamination"][0]["kind"] if r["contamination"] else None,
                "bad_license": r["license"] in ("UNKNOWN", "proprietary", "GPL-3.0-only"),
            }
        )
    return pl.DataFrame(records, schema=TRUTH_SCHEMA)


def documents(result: RunResult) -> pl.LazyFrame:
    """Every ingested document: kept ones from the shards, removed ones from the audit log."""
    if result.dataset_dir is None:
        raise RuntimeError("the run did not produce a dataset")
    kept = pl.scan_parquet(result.dataset_dir / "data" / "*.parquet").select(
        "document_id",
        "source_id",
        pl.col("provenance").struct.field("record_id").alias("record_id"),
        "flags",
        pl.lit(None, dtype=pl.String).alias("removed_by"),
    )
    removals = result.dataset_dir / "reports" / "removals.parquet"
    if not removals.is_file():
        return kept
    removed = pl.scan_parquet(removals).select(
        "document_id",
        "source_id",
        "record_id",
        pl.lit([], dtype=pl.List(pl.String)).alias("flags"),
        pl.col("stage").alias("removed_by"),
    )
    return pl.concat([kept, removed], how="vertical_relaxed")


def with_truth(frame: pl.LazyFrame, truth: pl.DataFrame) -> pl.DataFrame:
    keyed = frame.with_columns(pl.col("record_id").str.strip_prefix(RAW_PREFIX).alias("record_key"))
    return keyed.join(truth.lazy(), on=["source_id", "record_key"], how="left").collect()


def detected_ids(result: RunResult, artifact: str) -> list[str]:
    ids: set[str] = set()
    for segment in result.segments:
        for path in (segment.directory / "artifacts" / artifact).glob("part-*.jsonl"):
            ids.update(
                json.loads(line)["document_id"] for line in path.read_text(encoding="utf-8").splitlines() if line
            )
    return sorted(ids)


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(
    name: str,
    frame: pl.DataFrame,
    planted: pl.Expr,
    caught: pl.Expr,
    correct: pl.Expr | None = None,
    *,
    precision_only: bool = False,
    recall_only: bool = False,
) -> dict[str, Any]:
    """Recall over ``planted`` rows; precision over ``caught`` rows (``correct`` defaults to planted)."""
    planted_n = frame.filter(planted).height
    caught_rows = frame.filter(caught)
    hits = frame.filter(planted & caught).height
    good = caught_rows.filter(correct if correct is not None else planted).height
    return {
        "phenomenon": name,
        "planted": planted_n,
        "caught": caught_rows.height,
        "recall": None if precision_only else ratio(hits, planted_n),
        "precision": None if recall_only else ratio(good, caught_rows.height),
    }


def privacy_rows(result: RunResult) -> list[dict[str, Any]]:
    """What the PII and secret stages changed: detections, redacted spans, and bytes."""
    rows = []
    for stats in result.stats:
        if stats.name not in ("pii", "secrets"):
            continue
        rows.append(
            {
                "stage": stats.name,
                "documents_in": stats.documents_in,
                "documents_with_detections": stats.counters.get("documents_with_detections", 0),
                "spans_redacted": stats.counters.get("redactions", 0),
                "detections_by_type": {
                    key.split("|", 1)[1]: value
                    for key, value in sorted(stats.counters.items())
                    if key.startswith("detected|")
                },
                "bytes_in": stats.bytes_in,
                "bytes_out": stats.bytes_out,
                "removed": sum(stats.rejected.values()),
            }
        )
    return rows


def group_score(name: str, frame: pl.DataFrame, group: str, eligible: pl.Expr) -> dict[str, Any]:
    """A planted duplicate group counts as deduplicated when at most one member survives."""
    groups = (
        frame.filter(pl.col(group).is_not_null())
        .group_by(group)
        .agg(pl.col("removed_by").is_null().sum().alias("survivors"), eligible.any().alias("eligible"))
        .filter(pl.col("eligible"))
    )
    deduplicated = groups.filter(pl.col("survivors") <= 1).height
    return {
        "phenomenon": name,
        "planted": groups.height,
        "caught": deduplicated,
        "recall": ratio(deduplicated, groups.height),
        "precision": None,
    }


def analyse(sequential: RunResult, independent: RunResult, truth: pl.DataFrame) -> dict[str, Any]:
    seq = with_truth(documents(sequential), truth)
    ind = with_truth(documents(independent), truth)
    total = seq.height
    input_bytes = next(s for s in sequential.stats if s.name == "ingest").bytes_out
    removed_by = pl.col("removed_by")
    in_any_duplicate_group = pl.col("exact_group").is_not_null() | pl.col("near_group").is_not_null()

    sequential_rows = [
        {
            "stage": s.name,
            "removed": sum(s.rejected.values()),
            "removed_pct": ratio(sum(s.rejected.values()), total),
            "removed_mb": round(sum(s.rejected_bytes.values()) / 2**20, 3),
            "reasons": dict(s.rejected),
        }
        for s in sequential.stats
        if s.name != "ingest" and s.documents_in
    ]

    flags = ind.select(
        "document_id", pl.col("flags").list.eval(pl.element().str.split(":").list.first()).alias("filters")
    )
    exploded = flags.explode("filters").drop_nulls("filters").filter(pl.col("filters").is_in(FILTERS)).unique()
    counts = dict(exploded.group_by("filters").len().iter_rows())
    independent_rows = [
        {"filter": f, "flagged": counts.get(f, 0), "flagged_pct": ratio(counts.get(f, 0), total)} for f in FILTERS
    ]
    flagged_sets = {f: set(exploded.filter(pl.col("filters") == f)["document_id"]) for f in FILTERS}
    overlap = {a: {b: len(flagged_sets[a] & flagged_sets[b]) for b in FILTERS} for a in FILTERS}

    reached_privacy = removed_by.is_null() | ~removed_by.is_in(BEFORE_PRIVACY)
    pii_ids, secret_ids = detected_ids(sequential, "pii_detections"), detected_ids(sequential, "secret_detections")
    detection = [
        score(
            "non-English page -> language filter",
            seq,
            pl.col("non_english"),
            removed_by == "language",
            correct=pl.col("non_english") | pl.col("low_quality"),
        ),
        score("bad or unknown license -> license gate", seq, pl.col("bad_license"), removed_by == "license"),
        group_score("planted exact-duplicate group", seq, "exact_group", pl.lit(value=True)),
        group_score("planted near-duplicate group (J >= 0.85)", seq, "near_group", pl.col("near_jaccard") >= 0.85),
        score(
            "exact/near dedup removals in a planted group or templated junk page",
            seq,
            in_any_duplicate_group | pl.col("low_quality"),
            removed_by.is_in(["exact_dedup", "near_dedup"]),
            precision_only=True,
        ),
        score(
            "low-quality page removed by any stage",
            seq,
            pl.col("low_quality"),
            removed_by.is_not_null() & pl.col("low_quality"),
            recall_only=True,
        ),
        score(
            "quality-stage removals that are planted low-quality",
            seq,
            pl.col("low_quality"),
            removed_by == "quality",
            precision_only=True,
        ),
        score(
            "PII document -> PII detector",
            seq.filter(reached_privacy),
            pl.col("pii"),
            pl.col("document_id").is_in(pii_ids),
        ),
        score(
            "secret in code -> secret detector",
            seq.filter(reached_privacy),
            pl.col("secret"),
            pl.col("document_id").is_in(secret_ids),
        ),
        score(
            "benchmark contamination -> contamination stage",
            seq,
            pl.col("contamination").is_not_null(),
            removed_by == "contamination",
        ),
    ]
    by_kind = []
    for kind in ("exact", "substring", "paraphrase"):
        planted = seq.filter(pl.col("contamination") == kind)
        earlier = planted.filter(removed_by.is_not_null() & (removed_by != "contamination"))
        by_kind.append(
            {
                "kind": kind,
                "planted": planted.height,
                "removed_by_contamination": planted.filter(removed_by == "contamination").height,
                "removed_earlier_by": dict(earlier.group_by("removed_by").len().iter_rows()),
            }
        )
    near_below = (
        seq.filter(pl.col("near_group").is_not_null())
        .group_by("near_group")
        .agg(pl.col("near_jaccard").max())
        .filter(pl.col("near_jaccard") < 0.85)
        .height
    )
    return {
        "input_documents": total,
        "input_mb": round(input_bytes / 2**20, 3),
        "output_documents": seq.filter(removed_by.is_null()).height,
        "sequential": sequential_rows,
        "independent": independent_rows,
        "overlap": overlap,
        "detection": detection,
        "privacy": privacy_rows(sequential),
        "near_groups_below_threshold": near_below,
        "contamination_by_kind": by_kind,
        "sequential_elapsed_s": round(sequential.elapsed_s, 2),
        "independent_elapsed_s": round(independent.elapsed_s, 2),
    }


def markdown(results: dict[str, Any], scale: float, seed: int) -> str:
    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.1%}"

    detection_rows = [
        f"| {r['phenomenon']} | {r['planted']:,} | {r['caught']:,} | {pct(r['recall'])} | {pct(r['precision'])} |"
        for r in results["detection"]
    ]
    kind_rows = [
        f"| {r['kind']} | {r['planted']} | {r['removed_by_contamination']} | {r['removed_earlier_by'] or '-'} |"
        for r in results["contamination_by_kind"]
    ]
    privacy_table = [
        (
            f"| {r['stage']} | {r['documents_in']:,} | {r['documents_with_detections']:,} | "
            f"{r['spans_redacted']:,} | {r['bytes_in']:,} | {r['bytes_out']:,} | {r['removed']:,} |"
        )
        for r in results["privacy"]
    ]
    privacy_types = [
        f"- {r['stage']}: " + ", ".join(f"{kind} {count:,}" for kind, count in r["detections_by_type"].items())
        for r in results["privacy"]
        if r["detections_by_type"]
    ]
    lines = [
        "# Removal attribution - measured results",
        "",
        (
            f"Corpus: synthetic sample (seed {seed}, scale {scale}) - "
            f"{results['input_documents']:,} ingested documents, "
            f"{results['input_mb']} MB. Produced by `experiments/removal_attribution/run.py`; every number is measured."
        ),
        "",
        "## Sequential removal (pipeline order, filter mode)",
        "",
        "| Stage | Removed | % of input | MB removed |",
        "| :--- | ---: | ---: | ---: |",
        *(
            f"| {r['stage']} | {r['removed']:,} | {pct(r['removed_pct'])} | {r['removed_mb']} |"
            for r in results["sequential"]
        ),
        "",
        f"Output: {results['output_documents']:,} documents.",
        "",
        "## Independent flag rates (each filter sees the full corpus, annotate mode)",
        "",
        "| Filter | Flagged | % of input |",
        "| :--- | ---: | ---: |",
        *(f"| {r['filter']} | {r['flagged']:,} | {pct(r['flagged_pct'])} |" for r in results["independent"]),
        "",
        "## Overlap between filters (documents flagged by both, independent run)",
        "",
        "| | " + " | ".join(FILTERS) + " |",
        "| :--- | " + " | ".join("---:" for _ in FILTERS) + " |",
        *(f"| {a} | " + " | ".join(f"{results['overlap'][a][b]:,}" for b in FILTERS) + " |" for a in FILTERS),
        "",
        "## Privacy handling (sequential run, `policy: redact`)",
        "",
        "| Stage | Documents in | With detections | Spans redacted | Bytes in | Bytes out | Removed |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *privacy_table,
        "",
        "Detections by type (one document can contain several):",
        "",
        *privacy_types,
        "",
        "Redaction keeps the document and replaces each span with a typed placeholder such as `<EMAIL>`;",
        "under `policy: drop` the documents with detections would be removed instead.",
        "",
        "## Detection quality against planted ground truth (sequential run)",
        "",
        "| Phenomenon | Planted | Acted on | Recall | Precision |",
        "| :--- | ---: | ---: | ---: | ---: |",
        *detection_rows,
        "",
        "- *Planted* counts documents (or duplicate groups) carrying the label; *Acted on* counts what the",
        "  filter removed or detected. Recall is over planted items, precision over acted-on items.",
        "- The language filter also removes planted junk pages without English prose (keyword spam,",
        "  lorem ipsum, URL lists); its precision counts those as correct, so *Acted on* exceeds *Planted*.",
        "- Duplicate groups count as deduplicated when at most one member survives. Dedup precision also",
        "  counts removals of junk pages as correct: the generator builds them from a few templates, so they",
        "  can legitimately be near-duplicates of each other.",
        "- Quality-stage recall is not reported because earlier stages remove most junk pages first; the",
        "  *removed by any stage* row gives the end-to-end recall.",
        "- PII and secret recall only counts planted documents that reached those stages.",
        "",
        (
            f"Planted near-duplicate groups below the 0.85 threshold (correctly kept by definition): "
            f"{results['near_groups_below_threshold']}."
        ),
        "",
        "## Contamination recall by leak type",
        "",
        "| Leak type | Planted | Removed by contamination stage | Removed earlier by |",
        "| :--- | ---: | ---: | :--- |",
        *kind_rows,
        "",
        "Definitions are in the docstring of `run.py`. Ground truth comes from the generator, so these figures",
        "measure the filters on synthetic phenomena; they are not estimates of performance on real web data.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    configure_logging("WARNING")
    with tempfile.TemporaryDirectory() as tmp:
        project = prepare_project(Path(tmp), args.scale, args.seed)
        truth = ground_truth(project)
        options = RunOptions(use_cache=False)
        sequential = run_pipeline(write_config(project, "attribution-sequential", annotate=False), options)
        independent = run_pipeline(write_config(project, "attribution-independent", annotate=True), options)
        results = analyse(sequential, independent, truth)
    results["scale"], results["seed"] = args.scale, args.seed
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    (RESULTS / "RESULTS.md").write_text(markdown(results, args.scale, args.seed), encoding="utf-8", newline="\n")
    print((RESULTS / "RESULTS.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
