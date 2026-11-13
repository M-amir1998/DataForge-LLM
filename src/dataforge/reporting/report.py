"""Dataset reports: ``report.md``, machine-readable JSON summaries, tables, and plots.

Every number comes from the run itself: stage statistics, barrier summaries, per-part
artifacts, and DuckDB queries over the produced shards. Reports contain counts, types,
offsets, and hashes, never PII values, secrets, or benchmark text.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.config.loader import LoadedPipelineConfig
from dataforge.licensing.policy import LicensePolicy
from dataforge.pipelines.checkpoint import iter_jsonl
from dataforge.pipelines.graph import PipelinePlan
from dataforge.pipelines.stage import StageStats
from dataforge.provenance.manifest import DatasetManifest
from dataforge.reporting import svg
from dataforge.reporting.analysis import DatasetAnalytics
from dataforge.reporting.attribution import source_attribution
from dataforge.reporting.datacard import write_data_card
from dataforge.reporting.tables import fmt_bytes, fmt_int, fmt_pct, md_table

_ARTIFACTS = (
    "rejections",
    "ingest_errors",
    "pii_detections",
    "secret_detections",
    "contamination_matches",
)
_EXACT_METHODS = frozenset({"exact", "substring"})


@dataclass(slots=True)
class ReportContext:
    dataset_dir: Path
    manifest: DatasetManifest
    loaded: LoadedPipelineConfig
    plan: PipelinePlan
    stats: list[StageStats]
    barrier_summaries: dict[str, dict[str, Any]]
    segment_dirs: list[Path]
    elapsed_s: float
    peak_rss_bytes: int

    def stage(self, name: str) -> StageStats | None:
        return next((s for s in self.stats if s.name == name), None)


def iter_artifact(segment_dirs: list[Path], name: str) -> Iterator[dict[str, Any]]:
    for directory in segment_dirs:
        for path in sorted((directory / "artifacts" / name).glob("part-*.jsonl")):
            yield from iter_jsonl(path)


def iter_decision_table(segment_dirs: list[Path], name: str) -> Iterator[dict[str, Any]]:
    for directory in segment_dirs:
        path = directory / "decisions" / f"{name}.jsonl"
        if path.is_file():
            yield from iter_jsonl(path)


def _write_parquet(path: Path, rows: Iterator[dict[str, Any]], chunk: int = 50_000) -> int:
    writer: pq.ParquetWriter | None = None
    buffer: list[dict[str, Any]] = []
    written = 0
    try:
        for row in rows:
            buffer.append(row)
            if len(buffer) >= chunk:
                table = pa.Table.from_pylist(buffer)
                writer = writer or pq.ParquetWriter(path, table.schema, compression="zstd")
                writer.write_table(table)
                written += len(buffer)
                buffer = []
        if buffer:
            table = pa.Table.from_pylist(buffer)
            writer = writer or pq.ParquetWriter(path, table.schema, compression="zstd")
            writer.write_table(table)
            written += len(buffer)
    finally:
        if writer is not None:
            writer.close()
    return written


def _write_jsonl(path: Path, rows: Iterator[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _counter_split(counters: Counter[str], prefix: str) -> dict[str, int]:
    """``{"x": n}`` from counters named ``prefix|x``."""
    return {key[len(prefix) + 1 :]: int(value) for key, value in counters.items() if key.startswith(prefix + "|")}


# ----------------------------------------------------------------------------- sections


def _before_after(ctx: ReportContext) -> tuple[str, list[svg.Bar]]:
    rows: list[list[object]] = []
    bars: list[svg.Bar] = []
    ingest = ctx.stage("ingest")
    if ingest is not None:
        rows.append(
            [
                "ingest (input)",
                fmt_int(ingest.documents_out),
                f"{ingest.bytes_out / 2**20:,.2f}",
                "-",
                "-",
            ]
        )
        bars.append(svg.Bar("input", ingest.documents_out, fmt_int(ingest.documents_out)))
    for stats in ctx.stats:
        if stats.name == "ingest" or stats.documents_in == 0:
            continue
        removed = stats.documents_in - stats.documents_out
        top = stats.rejected.most_common(1)
        flagged = sum(stats.flagged.values())
        reason = top[0][0] if top else (f"{flagged:,} flagged (annotate)" if flagged else "-")
        rows.append(
            [
                stats.name,
                fmt_int(stats.documents_out),
                f"{stats.bytes_out / 2**20:,.2f}",
                fmt_pct(removed, stats.documents_in),
                reason,
            ]
        )
        bars.append(svg.Bar(stats.name, stats.documents_out, fmt_int(stats.documents_out)))
    table = md_table(["Stage", "Documents", "MB", "Removed %", "Top removal reason"], rows, "lrrrl")
    return table, bars


def _attribution(ctx: ReportContext, removals: Path) -> str:
    ingest = ctx.stage("ingest")
    inputs = _counter_split(ingest.counters, "documents") if ingest is not None else {}
    if not inputs:
        return "_No ingestion statistics recorded._"
    frame = source_attribution(removals, inputs)
    rows = [
        [
            row["source_id"],
            fmt_int(row["input_documents"]),
            fmt_int(row["removed_documents"]),
            fmt_bytes(row["removed_bytes"]),
            fmt_int(row["kept_documents"]),
            fmt_pct(row["kept_documents"], row["input_documents"]),
            row["top_stage"],
        ]
        for row in frame.iter_rows(named=True)
    ]
    return md_table(
        ["Source", "Input", "Removed", "Removed bytes", "Kept", "Kept %", "Top removing stage"], rows, "lrrrrrl"
    )


def _removal_reasons(ctx: ReportContext) -> str:
    rows = []
    for stats in ctx.stats:
        for reason, count in sorted(stats.rejected.items(), key=lambda item: (-item[1], item[0])):
            rows.append([stats.name, reason, fmt_int(count), fmt_bytes(stats.rejected_bytes.get(reason, 0))])
        for reason, count in sorted(stats.flagged.items()):
            rows.append([stats.name, f"{reason} (flagged only)", fmt_int(count), "-"])
    if not rows:
        return "_No documents were removed._"
    return md_table(["Stage", "Reason", "Documents", "Size"], rows, "llrr")


def _dedup(ctx: ReportContext) -> str:
    parts: list[str] = []
    exact = ctx.barrier_summaries.get("exact_dedup")
    if exact:
        parts.append(
            "**Exact deduplication** (normalized-content xxh3-128, policy "
            f"`{ctx.loaded.config.stages.exact_dedup.policy}`)\n"
        )
        parts.append(
            md_table(
                [
                    "Documents before",
                    "Documents after",
                    "Duplicates",
                    "Duplicate bytes",
                    "Groups",
                    "Largest group",
                ],
                [
                    [
                        fmt_int(exact["documents_before"]),
                        fmt_int(exact["documents_after"]),
                        fmt_int(exact["duplicates"]),
                        fmt_bytes(exact["duplicate_bytes"]),
                        fmt_int(exact["duplicate_groups"]),
                        fmt_int(exact["largest_group"]),
                    ]
                ],
                "rrrrrr",
            )
        )
    near = ctx.barrier_summaries.get("near_dedup")
    if near:
        cfg = ctx.loaded.config.stages.near_dedup
        parts.append(
            f"\n**Near-duplicate deduplication** (MinHash {cfg.num_perm} permutations, word {cfg.shingle_size}-gram "
            f"shingles, Jaccard >= {cfg.threshold}, LSH {near['bands']} bands x {near['rows_per_band']} rows, "
            f"policy `{cfg.policy}`)\n"
        )
        parts.append(
            md_table(
                [
                    "Documents before",
                    "Documents after",
                    "Duplicates",
                    "Duplicate bytes",
                    "Clusters",
                    "Largest cluster",
                    "Candidate pairs",
                    "Verified pairs",
                ],
                [
                    [
                        fmt_int(near["documents_before"]),
                        fmt_int(near["documents_after"]),
                        fmt_int(near["duplicates"]),
                        fmt_bytes(near["duplicate_bytes"]),
                        fmt_int(near["clusters"]),
                        fmt_int(near["largest_cluster"]),
                        fmt_int(near["candidate_pairs"]),
                        fmt_int(near["verified_pairs"]),
                    ]
                ],
                "rrrrrrrr",
            )
        )
    return "\n".join(parts) if parts else "_Deduplication stages were not enabled._"


def _quality(ctx: ReportContext) -> tuple[str, dict[str, Any]]:
    stats = ctx.stage("quality")
    if stats is None:
        return "_The quality stage was not enabled._", {}
    evaluated = _counter_split(stats.counters, "evaluated")
    failed = _counter_split(stats.counters, "failed")
    rows = [
        [
            name,
            fmt_int(evaluated[name]),
            fmt_int(failed.get(name, 0)),
            fmt_pct(failed.get(name, 0), evaluated[name]),
        ]
        for name in sorted(evaluated, key=lambda n: (-failed.get(n, 0), n))
    ]
    cfg = ctx.loaded.config.stages.quality
    summary = {
        "combine": cfg.combine,
        "min_score": cfg.min_score,
        "evaluated": evaluated,
        "failed": failed,
        "removed": dict(stats.rejected),
        "flagged": dict(stats.flagged),
    }
    text = (
        f"Combination policy: `{cfg.combine}`"
        + (f" (min score {cfg.min_score})" if cfg.combine == "min_score" else "")
        + ". A document fails a signal independently of the others; removals are attributed to the first "
        "failing signal in the documented order. Heuristic scores are not human quality judgements.\n\n"
        + md_table(["Signal", "Evaluated", "Failed", "Failure rate"], rows)
    )
    return text, summary


def _privacy(ctx: ReportContext) -> tuple[str, dict[str, Any]]:
    out: list[str] = []
    summary: dict[str, Any] = {}
    for stage_name, artifact, label in (
        ("pii", "pii_detections", "PII"),
        ("secrets", "secret_detections", "Secrets"),
    ):
        stats = ctx.stage(stage_name)
        if stats is None:
            continue
        policy = getattr(ctx.loaded.config.stages, stage_name).policy
        documents: dict[str, set[str]] = defaultdict(set)
        for row in iter_artifact(ctx.segment_dirs, artifact):
            documents[row["type"]].add(row["document_id"])
        detected = _counter_split(stats.counters, "detected")
        action = {"detect": "flagged", "redact": "redacted", "drop": "document dropped"}[policy]
        rows = [[kind, fmt_int(detected[kind]), fmt_int(len(documents[kind])), action] for kind in sorted(detected)]
        out.append(
            f"**{label}** (policy `{policy}`): {fmt_int(stats.counters.get('documents_with_detections', 0))} "
            f"documents with detections, {fmt_int(stats.counters.get('redactions', 0))} spans redacted, "
            f"{fmt_int(sum(stats.rejected.values()))} documents dropped.\n"
        )
        out.append(
            md_table(["Type", "Detections", "Documents", "Action"], rows, "lrrl") if rows else "_No detections._"
        )
        out.append("")
        summary[stage_name] = {
            "policy": policy,
            "detections": detected,
            "documents": {k: len(v) for k, v in sorted(documents.items())},
            "redactions": int(stats.counters.get("redactions", 0)),
            "dropped": int(sum(stats.rejected.values())),
        }
    if not out:
        return "_Privacy stages were not enabled._", summary
    out.append(
        "> Regex and checksum detectors are a first line of defence, not a compliance guarantee: they miss "
        "PII without a fixed shape (names, addresses) and can flag look-alikes. Reports never contain the "
        "detected values."
    )
    return "\n".join(out), summary


def _licenses(ctx: ReportContext) -> str:
    stats = ctx.stage("license")
    if stats is None:
        return "_The license stage was not enabled._"
    policy = LicensePolicy(ctx.loaded.config.stages.license)
    docs = _counter_split(stats.counters, "license")
    licenses = sorted({key.rsplit("|", 1)[0] for key in docs}, key=lambda lic: -docs.get(f"{lic}|documents", 0))
    rows = [
        [
            lic,
            fmt_int(docs.get(f"{lic}|documents", 0)),
            fmt_bytes(docs.get(f"{lic}|bytes", 0)),
            "yes" if policy.is_included(lic) else "no",
        ]
        for lic in licenses
    ]
    cfg = ctx.loaded.config.stages.license
    return (
        f"Unknown-license policy: `{cfg.unknown_policy}`. Licenses are taken from source metadata and are never "
        "inferred; this table describes configuration, not legal advice.\n\n"
        + md_table(["License", "Documents", "Bytes", "Included?"], rows, "lrrc")
    )


def _contamination(ctx: ReportContext) -> tuple[str, list[dict[str, Any]]]:
    if ctx.stage("contamination") is None:
        return "_The contamination stage was not enabled._", []
    exact: Counter[str] = Counter()
    near: Counter[str] = Counter()
    documents: dict[str, set[str]] = defaultdict(set)
    items: dict[str, set[str]] = defaultdict(set)
    for row in iter_artifact(ctx.segment_dirs, "contamination_matches"):
        benchmark = row["benchmark"]
        (exact if row["method"] in _EXACT_METHODS else near)[benchmark] += 1
        documents[benchmark].add(row["document_id"])
        items[benchmark].add(row["item_id"])
    rows = []
    benchmark_rows = []
    for entry in ctx.manifest.benchmarks:
        name = entry.name
        rows.append(
            [
                name,
                fmt_int(exact[name]),
                fmt_int(near[name]),
                fmt_int(len(documents[name])),
                fmt_int(len(items[name])),
                "yes" if entry.available else "not available locally",
            ]
        )
        benchmark_rows.append(
            {
                "benchmark": name,
                "exact": exact[name],
                "near": near[name],
                "documents_affected": len(documents[name]),
                "items_matched": len(items[name]),
            }
        )
    cfg = ctx.loaded.config.stages.contamination
    text = (
        f"Methods: {', '.join(cfg.methods)}; word {cfg.ngram_size}-gram containment >= {cfg.ngram_threshold}, "
        f"MinHash Jaccard >= {cfg.minhash_threshold}; policy `{cfg.policy}`. *Exact* counts exact and normalized "
        "substring matches; *Near* counts n-gram and MinHash matches. Evidence (`reports/contamination.jsonl`) is "
        "character offsets plus a short hash of the normalized benchmark field, never benchmark text.\n\n"
        + md_table(
            ["Benchmark", "Exact", "Near", "Documents affected", "Items matched", "Available"],
            rows,
            "lrrrrl",
        )
    )
    return text, benchmark_rows


def _distribution(rows: list[tuple[str, int, int, int]], label: str, total_docs: int) -> str:
    return md_table(
        [label, "Documents", "Text bytes", "Est. tokens", "Share of documents"],
        [[key, fmt_int(d), fmt_bytes(b), fmt_int(t), fmt_pct(d, total_docs)] for key, d, b, t in rows],
        "lrrrr",
    )


def _mixture(ctx: ReportContext) -> tuple[str, str | None]:
    plan = ctx.barrier_summaries.get("mixture")
    if not plan:
        return "_The mixture stage was not enabled._", None
    rows = [
        [
            d["domain"],
            f"{d['target_weight']:.1%}",
            f"{d['achieved_weight']:.1%}",
            fmt_int(d["available_units"]),
            fmt_int(d["selected_units"]),
            fmt_int(d["selected_documents"]),
            f"{d['sampling_rate']:.1%}",
        ]
        for d in plan["domains"]
    ]
    notes = "".join(f"\n- {note}" for note in plan.get("notes", []))
    text = (
        f"Unit: `{plan['unit']}`; unlisted domains: `{plan['unlisted_policy']}` ({fmt_int(plan['unlisted_documents'])} "
        f"documents). Selection takes documents in seeded-hash order within each domain, so it is deterministic "
        f"and stable when unrelated documents are added.{notes}\n\n"
        + md_table(
            [
                "Domain",
                "Target",
                "Achieved",
                f"Available ({plan['unit']})",
                f"Selected ({plan['unit']})",
                "Selected docs",
                "Sampling rate",
            ],
            rows,
            "lrrrrrr",
        )
    )
    chart = svg.grouped_bars(
        "Mixture: target vs achieved weight",
        f"Share of selected {plan['unit']} per domain",
        [d["domain"] for d in plan["domains"]],
        [
            ("target", [d["target_weight"] for d in plan["domains"]]),
            ("achieved", [d["achieved_weight"] for d in plan["domains"]]),
        ],
    )
    return text, chart


def _timings(ctx: ReportContext) -> str:
    rows = [[s.name, s.kind, f"{s.seconds:.2f}", fmt_int(s.documents_in or s.documents_out)] for s in ctx.stats]
    return md_table(["Stage", "Kind", "Seconds", "Documents processed"], rows, "llrr")


def write_reports(ctx: ReportContext) -> Path:
    """Write ``reports/`` (report.md, JSON summaries, tables, plots) and the dataset card."""
    reports = ctx.dataset_dir / "reports"
    plots = reports / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    manifest = ctx.manifest
    segments = ctx.segment_dirs

    removals = _write_parquet(reports / "removals.parquet", iter_artifact(segments, "rejections"))
    _write_jsonl(reports / "contamination.jsonl", iter_artifact(segments, "contamination_matches"))
    ingest_errors = _write_jsonl(reports / "ingest_errors.jsonl", iter_artifact(segments, "ingest_errors"))
    _write_parquet(reports / "exact_duplicates.parquet", iter_decision_table(segments, "exact_duplicates"))
    _write_parquet(
        reports / "near_duplicate_clusters.parquet",
        iter_decision_table(segments, "near_duplicate_clusters"),
    )

    before_after, funnel = _before_after(ctx)
    quality_text, quality_summary = _quality(ctx)
    privacy_text, privacy_summary = _privacy(ctx)
    contamination_text, contamination_rows = _contamination(ctx)
    mixture_text, mixture_chart = _mixture(ctx)
    execution = ctx.loaded.config.execution
    with DatasetAnalytics(
        ctx.dataset_dir,
        manifest,
        memory_limit_mb=execution.duckdb_memory_limit_mb,
        threads=execution.duckdb_threads,
    ) as analytics:
        domains = analytics.grouped("domain")
        sources = analytics.grouped("source_id")
        languages = analytics.grouped(
            "language",
            missing="CASE WHEN content_kind = 'code' THEN 'n/a (code)' ELSE 'undetermined' END",
        )
        lengths = analytics.length_histogram()
        quality_bins = analytics.quality_histogram()
        length_stats = analytics.length_stats()
    total_docs = manifest.totals.documents

    plot_files = {
        "stage_funnel.svg": svg.horizontal_bars(
            "Documents remaining after each stage",
            "Deduplication and filtering reduction, in pipeline order",
            funnel,
        ),
        "length_distribution.svg": svg.column_histogram(
            "Document length distribution",
            f"{fmt_int(total_docs)} output documents",
            lengths,
            x_label="characters per document (bin upper edge)",
        ),
        "quality_distribution.svg": svg.column_histogram(
            "Quality score distribution",
            "Weighted fraction of applicable heuristic signals passed",
            quality_bins,
            x_label="quality score (bin lower edge)",
        ),
        "domain_distribution.svg": svg.horizontal_bars(
            "Domain distribution",
            "Output documents per domain",
            [svg.Bar(k, d, fmt_int(d)) for k, d, _, _ in domains],
        ),
        "source_mixture.svg": svg.horizontal_bars(
            "Source mixture",
            "Estimated output tokens per source",
            [svg.Bar(k, t, svg.compact(t)) for k, _, _, t in sources],
        ),
    }
    if mixture_chart is not None:
        plot_files["mixture_weights.svg"] = mixture_chart
    for name, content in plot_files.items():
        (plots / name).write_text(content, encoding="utf-8", newline="\n")

    source_rows = [
        [
            s.source_id,
            s.type,
            f"per record (`{s.license_field}`)" if s.license_field else s.license,
            fmt_int(s.documents_read),
            fmt_int(s.record_errors),
            fmt_bytes(sum(f.bytes for f in s.files)) if s.files else "-",
        ]
        for s in manifest.sources
    ]
    split_rows = [
        [name, fmt_int(s.documents), fmt_int(s.tokens), fmt_int(s.shards), fmt_bytes(s.bytes)]
        for name, s in manifest.splits.items()
    ]
    shard_rows = [[s.path, s.split, fmt_int(s.documents), fmt_bytes(s.bytes), s.sha256[:12]] for s in manifest.shards]
    ingest = ctx.stage("ingest")
    input_docs = ingest.documents_out if ingest else 0
    input_bytes = ingest.bytes_out if ingest else 0
    throughput = input_bytes / 2**20 / ctx.elapsed_s if ctx.elapsed_s else 0.0

    lines = [
        f"# Data report: `{manifest.dataset_id}`",
        "",
        f"- **Created:** {manifest.created_at} (run `{manifest.run_id}`)",
        f"- **Pipeline config:** `{manifest.pipeline.config_path}` (hash `{manifest.pipeline.config_hash[:16]}`)",
        (
            f"- **Code:** DataForge {manifest.code.dataforge_version}, git `{(manifest.code.git_sha or 'n/a')[:12]}`"
            f"{' (dirty)' if manifest.code.git_dirty else ''}, code fingerprint `{manifest.code.code_fingerprint[:16]}`"
        ),
        f"- **Dataset fingerprint:** `{manifest.fingerprint[:24]}`",
        (
            f"- **Output:** {fmt_int(total_docs)} documents, {fmt_bytes(manifest.totals.text_bytes)} text, "
            f"{fmt_int(manifest.totals.tokens)} estimated tokens ({manifest.tokenizer}), "
            f"{manifest.totals.shards} shards"
        ),
        (
            f"- **Input:** {fmt_int(input_docs)} documents, {fmt_bytes(input_bytes)}; "
            f"kept {fmt_pct(total_docs, input_docs)}"
        ),
        (
            f"- **Processing time:** {ctx.elapsed_s:.1f} s ({throughput:.2f} MB/s of input), peak RSS "
            f"{ctx.peak_rss_bytes / 2**20:,.0f} MB"
        ),
        "",
        "## Sources",
        "",
        md_table(
            ["Source", "Type", "License", "Documents read", "Record errors", "Size"],
            source_rows,
            "lllrrr",
        ),
        "",
        f"Ingestion errors (structured, content-free): {fmt_int(ingest_errors)} - see `reports/ingest_errors.jsonl`.",
        "",
        "## Before / after",
        "",
        before_after,
        "",
        "![Documents remaining after each stage](plots/stage_funnel.svg)",
        "",
        "### Removal reasons",
        "",
        _removal_reasons(ctx),
        "",
        f"Every removal is auditable by document id in `reports/removals.parquet` ({fmt_int(removals)} rows).",
        "",
        "### Removals by source",
        "",
        _attribution(ctx, reports / "removals.parquet"),
        "",
        "## Deduplication",
        "",
        _dedup(ctx),
        "",
        "## Quality",
        "",
        quality_text,
        "",
        "![Quality score distribution](plots/quality_distribution.svg)",
        "",
        "## Privacy and secrets",
        "",
        privacy_text,
        "",
        "## Licensing",
        "",
        _licenses(ctx),
        "",
        "## Benchmark contamination",
        "",
        contamination_text,
        "",
        "## Domains",
        "",
        _distribution(domains, "Domain", total_docs),
        "",
        "![Domain distribution](plots/domain_distribution.svg)",
        "",
        "## Mixture",
        "",
        mixture_text,
        "",
    ]
    if mixture_chart is not None:
        lines += ["![Mixture weights](plots/mixture_weights.svg)", ""]
    lines += [
        "## Sources in the output",
        "",
        _distribution(sources, "Source", total_docs),
        "",
        "![Source mixture](plots/source_mixture.svg)",
        "",
        "## Languages in the output",
        "",
        _distribution(languages, "Language", total_docs),
        "",
        "## Token and length analysis",
        "",
        md_table(
            ["Documents", "Mean chars", "Median chars", "P90 chars", "Max chars", "Est. tokens"],
            [
                [
                    fmt_int(length_stats.get("documents", 0)),
                    f"{length_stats.get('mean_chars', 0):,.0f}",
                    f"{length_stats.get('median_chars', 0):,.0f}",
                    f"{length_stats.get('p90_chars', 0):,.0f}",
                    fmt_int(length_stats.get("max_chars", 0)),
                    fmt_int(length_stats.get("tokens", 0)),
                ]
            ],
            "rrrrrr",
        ),
        "",
        "![Document length distribution](plots/length_distribution.svg)",
        "",
        "## Splits",
        "",
        md_table(["Split", "Documents", "Est. tokens", "Shards", "Size"], split_rows, "lrrrr"),
        "",
        (
            "Splits are assigned per duplicate group (near-duplicate cluster, exact group, or content hash), so no "
            "group spans two splits; `reports/validation.json` records the leakage check."
        ),
        "",
        "## Output shards",
        "",
        md_table(["Shard", "Split", "Documents", "Size", "SHA-256"], shard_rows, "llrrl"),
        "",
        "## Processing time by stage",
        "",
        _timings(ctx),
        "",
        "## Reproducibility",
        "",
        f"- Re-run: `{manifest.reproduce['rerun']}`",
        f"- Verify against this manifest: `{manifest.reproduce['command']}`",
        "",
    ]
    report_path = reports / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    summary = {
        "dataset_id": manifest.dataset_id,
        "elapsed_s": round(ctx.elapsed_s, 3),
        "peak_rss_bytes": ctx.peak_rss_bytes,
        "input": {"documents": input_docs, "bytes": input_bytes},
        "output": manifest.totals.model_dump(),
        "stages": [s.as_dict() for s in ctx.stats],
        "barriers": ctx.barrier_summaries,
        "contamination": contamination_rows,
        "privacy": privacy_summary,
        "quality": quality_summary,
        "domains": [{"domain": k, "documents": d, "text_bytes": b, "tokens": t} for k, d, b, t in domains],
        "sources": [{"source_id": k, "documents": d, "text_bytes": b, "tokens": t} for k, d, b, t in sources],
        "languages": [{"language": k, "documents": d} for k, d, _, _ in languages],
        "length_histogram": lengths,
        "quality_histogram": quality_bins,
    }
    (reports / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (reports / "privacy.json").write_text(
        json.dumps(privacy_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (reports / "quality.json").write_text(
        json.dumps(quality_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    write_data_card(ctx.dataset_dir / "DATASET_CARD.md", ctx, domains, languages)
    return report_path
