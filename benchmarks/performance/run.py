"""DataForge performance benchmarks (CPU only, synthetic data, nothing downloaded).

Usage (from the repository root):
    uv run python benchmarks/performance/run.py exact-dedup --documents 200000
    uv run python benchmarks/performance/run.py near-dedup --documents 50000
    uv run python benchmarks/performance/run.py quality --documents 50000
    uv run python benchmarks/performance/run.py parquet-write --documents 50000
    uv run python benchmarks/performance/run.py pipeline --scale 5
    uv run python benchmarks/performance/run.py streaming-memory --gigabytes 17
    uv run python benchmarks/performance/run.py all --quick --output reports/benchmarks.json

Every result records the machine, Python and library versions, input size, wall time,
throughput, and memory (RSS sampled at batch boundaries plus the OS-reported peak).
Results are appended to benchmarks/performance/results/<name>.json; RESULTS.md is
regenerated from those files, so it only ever contains measured numbers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gc
import importlib.metadata
import json
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import psutil
import pyarrow.parquet as pq
import yaml

from dataforge.config.loader import ResolvedSource
from dataforge.config.models import NearDedupConfig, NormalizeConfig, QualityConfig
from dataforge.config.sources import SyntheticSourceConfig
from dataforge.dedup.exact import ExactDeduplicator
from dataforge.dedup.near import NearDeduplicator
from dataforge.domain.document import Document
from dataforge.ingestion.ingest import Ingestor, record_to_document
from dataforge.normalization.text import TextNormalizer
from dataforge.parsing.base import ParserRegistry
from dataforge.pipelines.runner import RunOptions, run_pipeline
from dataforge.quality.scorer import QualityScorer
from dataforge.sources.base import RawRecord
from dataforge.sources.synthetic import SyntheticSourceReader
from dataforge.storage.schema import DOCUMENT_SCHEMA, documents_to_table
from dataforge.synthetic.corpus import generate_sample_corpus
from dataforge.telemetry.metrics import MemorySampler, os_peak_rss_bytes, rss_bytes

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
MB = 1024**2
GIB = 1024**3
PARSERS = ParserRegistry()


def environment() -> dict[str, Any]:
    versions = {name: importlib.metadata.version(name) for name in ("pyarrow", "duckdb", "polars", "numpy", "xxhash")}
    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "cpu": platform.processor() or "unknown",
        "logical_cores": psutil.cpu_count(logical=True),
        "ram_gib": round(psutil.virtual_memory().total / GIB, 1),
        "versions": versions,
    }


def synthetic_documents(
    count: int | None = None, total_bytes: int | None = None, seed: int = 0
) -> Iterator[list[Document]]:
    config = SyntheticSourceConfig(
        type="synthetic", num_documents=count, total_bytes=total_bytes, seed=seed, mean_words=350
    )
    ingestor = Ingestor([SyntheticSourceReader(ResolvedSource("synthetic", config, ROOT))])
    for batch in ingestor.iter_batches(1024):
        yield batch.documents


def materialize(count: int, seed: int = 0) -> list[Document]:
    return [doc for batch in synthetic_documents(count, seed=seed) for doc in batch]


def measure(name: str, work: Callable[[MemorySampler], dict[str, Any]]) -> dict[str, Any]:
    gc.collect()
    sampler = MemorySampler()
    baseline = sampler.sample()
    started = time.perf_counter()
    details = work(sampler)
    seconds = time.perf_counter() - started
    sampler.sample()
    documents = details.get("documents", 0)
    text_bytes = details.get("text_bytes", 0)
    result = {
        "benchmark": name,
        "timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "seconds": round(seconds, 3),
        "documents_per_second": round(documents / seconds, 1) if seconds else None,
        "mb_per_second": round(text_bytes / MB / seconds, 3) if seconds else None,
        "baseline_rss_mb": round(baseline / MB, 1),
        "peak_rss_sampled_mb": round(sampler.peak_bytes / MB, 1),
        "os_peak_rss_mb": round((os_peak_rss_bytes() or 0) / MB, 1),
        "environment": environment(),
        **details,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "environment"}, indent=2))
    return result


# --------------------------------------------------------------------------- benchmarks


def bench_exact(documents: int) -> dict[str, Any]:
    docs = materialize(documents, seed=1)

    def work(sampler: MemorySampler) -> dict[str, Any]:
        dedup = ExactDeduplicator("keep_first")
        observe_started = time.perf_counter()
        for start in range(0, len(docs), 1024):
            dedup.observe(docs[start : start + 1024])
            sampler.sample()
        observe_seconds = time.perf_counter() - observe_started
        finalize_started = time.perf_counter()
        result, _ = dedup.finalize()
        return {
            "documents": len(docs),
            "text_bytes": sum(d.num_bytes for d in docs),
            "observe_seconds": round(observe_seconds, 3),
            "finalize_seconds": round(time.perf_counter() - finalize_started, 3),
            "duplicates_found": result.duplicates,
            "measures": (
                "grouping by the content hashes computed during ingestion and normalization; "
                "text is not re-hashed, so MB/s describes the input, not hashing speed"
            ),
        }

    return measure("exact-dedup", work)


def bench_near(documents: int) -> dict[str, Any]:
    docs = materialize(documents, seed=2)

    def work(sampler: MemorySampler) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            dedup = NearDeduplicator(NearDedupConfig(), Path(tmp))
            observe_started = time.perf_counter()
            for start in range(0, len(docs), 1024):
                dedup.observe(docs[start : start + 1024])
                sampler.sample()
            observe_seconds = time.perf_counter() - observe_started
            finalize_started = time.perf_counter()
            result, _ = dedup.finalize()
            finalize_seconds = time.perf_counter() - finalize_started
            dedup.discard()
        return {
            "documents": len(docs),
            "text_bytes": sum(d.num_bytes for d in docs),
            "signature_seconds": round(observe_seconds, 3),
            "finalize_seconds": round(finalize_seconds, 3),
            "num_perm": 128,
            "bands_rows": [result.bands, result.rows],
            "clusters": result.clusters,
            "duplicates_found": result.duplicates,
        }

    return measure("near-dedup", work)


def bench_quality(documents: int) -> dict[str, Any]:
    docs = materialize(documents, seed=3)
    normalizer = TextNormalizer(NormalizeConfig())
    for doc in docs:
        doc.text = normalizer.normalize(doc.text).text

    def work(sampler: MemorySampler) -> dict[str, Any]:
        scorer = QualityScorer(QualityConfig())
        passed = 0
        for start in range(0, len(docs), 1024):
            passed += sum(a.passed for a in scorer.assess_batch(docs[start : start + 1024]))
            sampler.sample()
        return {"documents": len(docs), "text_bytes": sum(d.num_bytes for d in docs), "passed": passed}

    return measure("quality", work)


def bench_parquet(documents: int) -> dict[str, Any]:
    docs = materialize(documents, seed=4)
    text_bytes = sum(d.num_bytes for d in docs)
    convert_started = time.perf_counter()
    table = documents_to_table(docs)
    convert_seconds = time.perf_counter() - convert_started
    codecs: list[tuple[str, int | None]] = [
        ("none", None),
        ("snappy", None),
        ("lz4", None),
        ("zstd", 1),
        ("zstd", 3),
        ("zstd", 9),
        ("gzip", 6),
    ]

    def work(sampler: MemorySampler) -> dict[str, Any]:
        rows = []
        with tempfile.TemporaryDirectory() as tmp:
            for codec, level in codecs:
                path = Path(tmp) / f"{codec}-{level}.parquet"
                started = time.perf_counter()
                pq.write_table(
                    table,
                    path,
                    compression=None if codec == "none" else codec,
                    compression_level=level,
                    row_group_size=2000,
                )
                write_seconds = time.perf_counter() - started
                started = time.perf_counter()
                pq.read_table(path, columns=["text", "document_id"])
                read_seconds = time.perf_counter() - started
                size = path.stat().st_size
                rows.append({
                    "codec": codec if level is None else f"{codec}({level})",
                    "file_mb": round(size / MB, 2),
                    "ratio_vs_text": round(text_bytes / size, 2),
                    "write_mb_per_s": round(text_bytes / MB / write_seconds, 1),
                    "read_mb_per_s": round(text_bytes / MB / read_seconds, 1),
                })  # fmt: skip
                sampler.sample()
        return {
            "documents": len(docs),
            "text_bytes": text_bytes,
            "arrow_conversion_seconds": round(convert_seconds, 3),
            "schema_columns": len(DOCUMENT_SCHEMA),
            "codecs": rows,
        }

    return measure("parquet-write", work)


def bench_pipeline(scale: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        shutil.copytree(ROOT / "configs", project / "configs")
        shutil.copytree(ROOT / "benchmarks" / "contamination", project / "benchmarks" / "contamination")
        generate_sample_corpus(project / "data" / "smoke", scale=scale)
        config = yaml.safe_load((project / "configs" / "pipelines" / "smoke.yaml").read_text(encoding="utf-8"))
        config["base_dir"] = "."
        config["sources"]["registry"] = "configs/sources/smoke.yaml"
        config["execution"] = {"batch_size": 256, "part_max_documents": 5000}
        config["stages"]["mixture"]["enabled"] = False
        path = project / "bench.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

        def work(sampler: MemorySampler) -> dict[str, Any]:
            result = run_pipeline(path, RunOptions(use_cache=False))
            sampler.sample()
            ingest = next(s for s in result.stats if s.name == "ingest")
            return {
                "scale": scale,
                "documents": ingest.documents_out,
                "text_bytes": ingest.bytes_out,
                "output_documents": next(s for s in reversed(result.stats)).documents_out,
                "pipeline_peak_rss_mb": round(result.peak_rss_bytes / MB, 1),
                "stage_seconds": {s.name: round(s.seconds, 3) for s in result.stats},
            }

        return measure("pipeline", work)


def recycled_stream(total_bytes: int, pool_size: int = 4000, batch_size: int = 1024) -> Iterator[list[Document]]:
    """Distinct documents at low generation cost, through the real ingestion path.

    Text comes from a pool of generated documents; every emitted record carries a unique
    identifier line, so each document has its own id and content hash and is normalized,
    hashed, and keyed like any other. This keeps synthetic-text generation (not part of
    DataForge) out of the measurement.
    """
    pool = [doc.text for doc in materialize(pool_size, seed=11)]
    source = ResolvedSource("stream", SyntheticSourceConfig(type="synthetic", num_documents=1), ROOT)
    produced = index = 0
    batch: list[Document] = []
    while produced < total_bytes:
        text = f"Record {index:012d}\n\n{pool[index % pool_size]}"
        record = RawRecord("stream", "synthetic://stream", f"stream#{index}", text, "text/plain", record_id=str(index))
        document = record_to_document(record, source, PARSERS)
        batch.append(document)
        produced += document.num_bytes
        index += 1
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def bench_streaming_memory(gigabytes: float) -> dict[str, Any]:
    """Stream a corpus larger than physical RAM through ingestion, normalization, and dedup keys.

    Nothing is written to disk. Two memory figures are reported: the streaming path (flat
    RSS) and the exact-dedup key store, which by design grows by a fixed number of bytes per
    document (not per byte of text).
    """
    target = int(gigabytes * GIB)

    def work(sampler: MemorySampler) -> dict[str, Any]:
        normalizer = TextNormalizer(NormalizeConfig())
        dedup = ExactDeduplicator("keep_first")
        processed_bytes = documents = 0
        checkpoints: list[dict[str, float]] = []
        next_report = 0.05
        started = time.perf_counter()
        for batch in recycled_stream(target):
            for doc in batch:
                result = normalizer.normalize(doc.text)
                if result.changed:
                    doc.replace_text(result.text, "normalize")
            dedup.observe(batch)
            documents += len(batch)
            processed_bytes += sum(d.num_bytes for d in batch)
            current = sampler.sample()
            if processed_bytes >= next_report * target:
                checkpoints.append({
                    "progress": round(processed_bytes / target, 3),
                    "gib_processed": round(processed_bytes / GIB, 3),
                    "rss_mb": round(current / MB, 1),
                    "elapsed_s": round(time.perf_counter() - started, 1),
                })  # fmt: skip
                print(json.dumps(checkpoints[-1]), flush=True)
                next_report += 0.05
            del batch
        keys_bytes = documents * (8 + 8 + 8 + 4 + 32)
        return {
            "documents": documents,
            "text_bytes": processed_bytes,
            "gib_processed": round(processed_bytes / GIB, 3),
            "physical_ram_gib": round(psutil.virtual_memory().total / GIB, 1),
            "exceeds_physical_ram": processed_bytes > psutil.virtual_memory().total,
            "exact_dedup_key_store_mb": round(keys_bytes / MB, 1),
            "rss_checkpoints": checkpoints,
        }

    return measure("streaming-memory", work)


# --------------------------------------------------------------------------- results


def save(result: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{result['benchmark']}.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append(result)
    path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_markdown()


_SUMMARY_KEYS = frozenset(
    {
        "benchmark", "timestamp", "seconds", "documents_per_second", "mb_per_second", "baseline_rss_mb",
        "peak_rss_sampled_mb", "os_peak_rss_mb", "environment", "documents", "text_bytes", "codecs", "rss_checkpoints",
    }
)  # fmt: skip


def _row(cells: Sequence[object]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _section(result: dict[str, Any]) -> list[str]:
    env = result["environment"]
    machine = f"{env['platform']}, {env['logical_cores']} logical cores, {env['ram_gib']} GiB RAM"
    memory = (
        f"- Memory: baseline {result['baseline_rss_mb']} MB, peak sampled RSS {result['peak_rss_sampled_mb']} MB, "
        f"OS peak {result['os_peak_rss_mb']} MB"
    )
    lines = [
        f"## {result['benchmark']}",
        "",
        f"- When: {result['timestamp']} on {machine}, Python {env['python']}",
        f"- Input: {result.get('documents', 0):,} documents, {result.get('text_bytes', 0) / MB:,.1f} MB of text",
        f"- Wall time: {result['seconds']} s ({result['documents_per_second']} docs/s, {result['mb_per_second']} MB/s)",
        memory,
    ]
    lines += [f"- {key}: {value}" for key, value in result.items() if key not in _SUMMARY_KEYS]
    if "codecs" in result:
        lines += [
            "",
            _row(["Codec", "File MB", "Text/file ratio", "Write MB/s", "Read MB/s"]),
            _row([":---"] + ["---:"] * 4),
        ]
        lines += [
            _row([c["codec"], c["file_mb"], c["ratio_vs_text"], c["write_mb_per_s"], c["read_mb_per_s"]])
            for c in result["codecs"]
        ]
    if "rss_checkpoints" in result:
        lines += ["", _row(["Progress", "GiB processed", "RSS MB", "Elapsed s"]), _row(["---:"] * 4)]
        lines += [
            _row([f"{c['progress']:.0%}", c["gib_processed"], c["rss_mb"], c["elapsed_s"]])
            for c in result["rss_checkpoints"]
        ]
    return [*lines, ""]


def write_markdown() -> None:
    lines = [
        "# Benchmark results",
        "",
        "Generated by `benchmarks/performance/run.py` from the JSON files in this directory.",
        "Only measured runs appear here; anything not run is absent.",
        "",
    ]
    for path in sorted(RESULTS.glob("*.json")):
        lines += _section(json.loads(path.read_text(encoding="utf-8"))[-1])
    (RESULTS / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "benchmark",
        choices=["exact-dedup", "near-dedup", "quality", "parquet-write", "pipeline", "streaming-memory", "all"],
    )
    parser.add_argument("--documents", type=int, default=20_000)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--gigabytes", type=float, default=0.25)
    parser.add_argument("--quick", action="store_true", help="small sizes for CI smoke benchmarking")
    parser.add_argument("--output", type=Path, help="also write this run's results to a JSON file")
    parser.add_argument("--no-save", action="store_true", help="do not append to benchmarks/performance/results")
    args = parser.parse_args(argv)
    documents = 2_000 if args.quick else args.documents
    plan: dict[str, Callable[[], dict[str, Any]]] = {
        "exact-dedup": lambda: bench_exact(documents),
        "near-dedup": lambda: bench_near(documents),
        "quality": lambda: bench_quality(documents),
        "parquet-write": lambda: bench_parquet(documents),
        "pipeline": lambda: bench_pipeline(0.2 if args.quick else args.scale),
        "streaming-memory": lambda: bench_streaming_memory(0.02 if args.quick else args.gigabytes),
    }
    selected = list(plan) if args.benchmark == "all" else [args.benchmark]
    results = [plan[name]() for name in selected]
    if not (args.no_save or args.quick):
        for result in results:
            save(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"rss now {rss_bytes() / MB:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
