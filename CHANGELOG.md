# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-13

### Added

- Canonical, versioned document schema (Arrow/Parquet) with per-document provenance and
  transformation history; stable content-derived document ids.
- Typed YAML configuration with fail-early validation, stage includes, `base_dir` path
  resolution, and an output-affecting config hash.
- Source registry with local (text, JSONL, JSON, CSV, Parquet, gzip), code-tree, Hugging Face
  (streaming, optional), and synthetic readers; in-place archive streaming with traversal and
  decompression-bomb protection.
- Streaming ingestion with resumable cursors and structured record errors.
- Idempotent text normalization that preserves code formatting; code detection; built-in
  language identification behind a pluggable detector port.
- Exact deduplication, Bloom-filter paragraph deduplication, and MinHash/LSH near
  deduplication with recall-constrained band tuning and deterministic representatives.
- PII detection with checksum validation and credential detection with entropy gating; detect,
  redact, and drop policies.
- License normalization (never inferred) and license / usage-policy filtering.
- Fifteen heuristic quality signals with combination policies and a learned-model plug-in
  point.
- Benchmark contamination detection (exact, normalized substring, n-gram containment,
  MinHash) with an offline benchmark registry and leak-free evidence.
- Rule-based domain classification, deterministic token / byte / document mixtures, and
  group-aware train / validation / test splits (hash, source, domain, time).
- Pipeline DAG with streaming and barrier stages, checkpointed segments, resume, segment
  caching, and immutable content-addressed dataset versions published only after validation.
- Size-bounded Parquet and zstd-JSONL shards with Hugging Face naming; schema-versioned
  manifests; dataset validation (hashes, counts, schema, id uniqueness, split leakage).
- Markdown reports, dataset cards, accessible SVG plots, a removal audit log, and per-source
  removal attribution.
- `dataforge` CLI: doctor, sources, sample, ingest, dedup, quality, privacy, contamination,
  mixture, dataset (validate / inspect / read-only SQL), pipeline (plan / run / reproduce),
  report, and cache.
- Deterministic synthetic sample corpus with ground-truth labels and synthetic CC0 demo
  benchmarks.
- Unit, property-based, integration, and end-to-end test suites.
- Jenkins pipeline, Docker images (runtime and CI agent), optional Compose stack, pre-commit
  hooks, repository secret scanner, and distribution checks.
- Documentation: architecture, methodology, governance, CI, and architecture decision records.
