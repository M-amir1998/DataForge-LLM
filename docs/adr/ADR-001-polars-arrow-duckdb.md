# ADR-001: Arrow/Parquet, DuckDB, and Polars, each for a defined job

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

The pipeline must process corpora larger than memory on a 16 GB laptop, write model-ready
columnar data, validate datasets without loading them, and produce analytical reports. Three
mature libraries overlap in capability: Apache Arrow (with Parquet), DuckDB, and Polars.
Using all of them everywhere would blur ownership and multiply failure modes; using one for
everything would force awkward fits.

Most per-document work (normalization, regex detectors, quality heuristics, language and
domain classification) is inherently per-string Python logic. Deduplication and mixture
selection are array problems over compact per-document keys.

## Decision

Each library has a defined job:

| Library | Job | Where |
| :--- | :--- | :--- |
| Arrow / Parquet | Canonical schema, the on-disk format of intermediate parts and shards, zstd streams, reading Parquet sources | `storage.schema`, `storage.parts`, `sharding.writer`, `sources.local` |
| DuckDB | Out-of-core SQL over Parquet: dataset validation (id uniqueness, split leakage), report distributions, `dataset inspect`, read-only `dataset sql` | `validation.dataset`, `reporting.analysis`, `inspection` |
| Polars | Lazy dataframe transformations over audit and report tables; currently the per-source removal attribution (`pl.scan_parquet` over `reports/removals.parquet`, grouped by source and stage, joined with per-source ingest counts) and the removal-attribution experiment analysis | `reporting.attribution`, `experiments/` |
| NumPy | Vectorized MinHash, LSH banding, union-find roots, exact-dedup grouping, mixture selection | `dedup`, `mixtures` |

Polars is deliberately **not** used in the per-document hot path: converting batches between
dataclasses and dataframes for regex-heavy per-string logic would add copies without removing
the Python-level work. DuckDB connections always set an explicit `memory_limit` and thread
count, because DuckDB defaults to most of the machine's memory and every core.

## Consequences

- Clear ownership: schema questions are Arrow questions; aggregate questions over finished data
  are DuckDB or Polars questions; dedup math is NumPy.
- Datasets are plain Parquet, readable by any of these tools (and by Hugging Face `datasets`).
- Three analytical dependencies add install size. Polars currently covers a small surface; if
  it is not used more broadly (for example to vectorize simple quality signals where profiling
  shows a benefit), its role should be revisited rather than kept by default.
- Hot-path throughput is bounded by Python per-document processing; the scale-out path is
  parallel batches ([scale-out](../architecture/scale-out.md)), not a different dataframe
  engine.

## Alternatives considered

- **pandas:** row-oriented in-memory frames, poor out-of-core story, and higher memory per
  string. Rejected.
- **Polars for every stage:** attractive for vectorizable signals, but most stages are regex
  and branching logic per document; the conversion cost and readability loss were not
  justified without measurements.
- **DuckDB for dedup grouping:** works out of core, but NumPy over fixed-width keys is simpler
  and fast at laptop scale; DuckDB remains the option for corpora whose keys exceed memory.
- **Spark or Ray locally:** heavyweight for a single machine; kept as a scale-out option only.
