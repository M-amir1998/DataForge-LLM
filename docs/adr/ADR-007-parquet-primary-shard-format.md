# ADR-007: Parquet as the primary shard format

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Model-ready shards are read by training data loaders, by analysts, and by validation
tooling. They carry text plus structured metadata (maps, lists, nested provenance). The
format must compress well, support schema metadata, allow column projection, and be readable
without DataForge.

## Decision

- **Parquet is the default shard format**, written with zstd (level 3 by default), row
  groups of `row_group_documents` (2,000 by default), and the canonical schema version in
  file metadata.
- Shards are size-bounded by document count and compressed size and named
  `<split>-<index>-of-<count>.parquet`, the Hugging Face convention.
- **zstd-compressed JSONL** (`shard.format: jsonl`) is supported for consumers that need
  line-oriented text.
- WebDataset-style tar shards are not provided: the payload is text, not media.

## Consequences

- Readers can project only `text`, or only metadata, without reading the rest.
- Nested provenance and annotation columns are stored natively instead of as strings.
- DuckDB, Polars, PyArrow, Spark, and Hugging Face `datasets` read the shards directly.
- Compressed-size targeting works at row-group granularity; a shard can exceed the target by
  up to one row group plus its footer.
- JSONL shards get the same validation as Parquet: DuckDB reads the zstd JSONL directly for
  the id-uniqueness and split-leakage checks, and the content-hash spot check streams the
  first records. Parquet remains faster to validate because the checks read only key columns.

## Alternatives considered

- **JSONL only:** universal and streamable, but larger, untyped, and slow to analyze; kept as
  an option.
- **Arrow IPC / Feather:** fast, but weaker ecosystem support for long-term storage and
  compression.
- **WebDataset tar:** designed for large binary samples; adds no value for text.
- **TFRecord:** tied to one framework.
