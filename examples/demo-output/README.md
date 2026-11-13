# Example output: the demo dataset

This directory is a complete, unmodified dataset version produced by

```bash
uv run dataforge sample generate data/sample
uv run dataforge pipeline run configs/pipelines/demo.yaml --no-cache
```

on the synthetic sample corpus (seed 7, scale 1.0). It is committed so that the outputs can be
inspected without running anything; it contains only synthetic text, with PII and credentials
redacted by the pipeline.

| | |
| :--- | :--- |
| Dataset | `demo-pretraining-e5a12d56cebd` |
| Run | `20261113T215000Z-1c849f` |
| Code | DataForge 0.1.0, git `6e9c3f1` (clean tree), code fingerprint `2f7d2c1c586ac088` |
| Result | 1,501 input documents, 469 output documents in 4 Parquet shards |

## Contents

| Path | What it is |
| :--- | :--- |
| [`manifest.json`](manifest.json) | Identity, code and config lineage, sources with file SHA-256s, shard digests, per-stage statistics |
| [`DATASET_CARD.md`](DATASET_CARD.md) | Dataset card: composition, licenses, processing, known limitations |
| `data/*.parquet` | The model-ready shards (`train`, `validation`, `test`) |
| [`reports/report.md`](reports/report.md) | Pipeline report: funnel, removal reasons, dedup, privacy, quality, contamination, mixture, plots |
| `reports/plots/*.svg` | Charts used by the report |
| [`reports/validation.json`](reports/validation.json) | The nine integrity checks run before publishing |
| [`reports/summary.json`](reports/summary.json) | Machine-readable summary of the report |
| `reports/removals.parquet` | One row per removed document: id, source, record, stage, reason, bytes |
| `reports/exact_duplicates.parquet`, `reports/near_duplicate_clusters.parquet` | Duplicate groups and near-duplicate clusters |
| [`reports/quality.json`](reports/quality.json), [`reports/privacy.json`](reports/privacy.json) | Quality signal pass/fail counts; PII and credential detection counts by type |
| [`reports/contamination.jsonl`](reports/contamination.jsonl) | Benchmark matches as offsets and hashes (no benchmark text) |
| [`reports/ingest_errors.jsonl`](reports/ingest_errors.jsonl) | Malformed input records, by locator: the three broken JSON lines the generator plants (its fourth corrupted record, a Latin-1 line, is decoded with replacement characters and kept) |

## Try it

```bash
uv run dataforge dataset validate examples/demo-output
uv run dataforge dataset inspect examples/demo-output
uv run dataforge dataset sql examples/demo-output "SELECT split, count(*) AS documents FROM docs GROUP BY split"
```

To rebuild it from scratch and compare every shard byte for byte:

```bash
uv run dataforge sample generate data/sample
uv run dataforge pipeline reproduce examples/demo-output/manifest.json
```

A bit-identical rebuild needs the locked library versions from `uv.lock`; the command refuses
to run if the sample corpus or the pipeline configuration differs from what the manifest
records.
