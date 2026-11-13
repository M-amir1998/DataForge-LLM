# Storage

## Library roles

| Library | Used for |
| :--- | :--- |
| Apache Arrow / Parquet (`pyarrow`) | The canonical document schema, intermediate parts, model-ready shards, source Parquet reading, zstd streams |
| DuckDB | Dataset validation (id uniqueness, split leakage), report distributions, `dataset inspect`, read-only `dataset sql`; always with an explicit `memory_limit` and thread count |
| Polars | Lazy dataframe transformations over audit and report tables, e.g. the "Removals by source" table built from `reports/removals.parquet` |
| NumPy | Vectorized MinHash, LSH banding, union-find, exact-dedup grouping, mixture selection arrays |

Per-document text processing (normalization, detectors, quality signals) is plain Python
over slotted dataclasses; no dataframe library sits in that hot path. See
[ADR-001](../adr/ADR-001-polars-arrow-duckdb.md).

## Canonical document schema

`dataforge.storage.schema.DOCUMENT_SCHEMA` is the one schema used for intermediate parts and
output shards. Columns: `document_id`, `source_id`, `text` (large string), `language`,
`language_confidence`, `mime_type`, `content_kind`, `programming_language`, `license`,
`license_source`, `usage_policy`, `url`, `title`, `timestamp`, `content_hash`, `num_chars`,
`num_bytes`, `token_estimate`, `domain`, `domain_confidence`, `quality_score`,
`quality_signals` (map string to float64), `pii_counts` and `secret_counts` (map string to
int32), `exact_dup_group`, `near_dup_cluster`, `near_dup_similarity`, `contamination` (list of
benchmark / item / method / score structs), `flags` (list of strings), `split`, `extra`
(JSON string of passthrough source fields), and `provenance` (struct of source URI, record
locator, record id, source version, source fingerprint, and the list of transformations).

Floating-point scores are stored as float64 so documents round-trip losslessly.

### Schema versioning and evolution

The schema version (currently `1.0`) is written into every Parquet file as key-value
metadata `dataforge.document_schema_version`, and into every manifest as
`document_schema_version`. Validation rejects shards whose Arrow schema differs from the
canonical one or that lack the version key. The manifest carries its own `schema_version`
(`1.0`); readers refuse unknown versions.

Evolution policy (no migration framework exists yet, by design):

- **Additive changes** (a new nullable column) bump the minor version. Readers must
  tolerate unknown trailing columns.
- **Breaking changes** (rename, type change, removal) bump the major version and ship with a
  documented, tested conversion that rewrites shards and manifests into a new dataset version.
  Existing versions are never modified in place.

## Work directory

```text
<work_dir>/
  segments/seg<NN>-<fingerprint prefix>/   parts, artifacts, state, decisions, spill, _progress.json, _SUCCESS
  staging/<run_id>/                       the dataset being assembled before validation
  runs/<run_id>/                          run.json, events.jsonl
  reproduce/<version>/                     outputs of `pipeline reproduce`
```

Intermediate parts are `part-<NNNNN>.parquet` files compressed with
`storage.intermediate_compression` (`zstd` by default). A part is written as `.tmp` and
renamed when its checkpoint commits, so a truncated part can never look complete.

## Datasets and shards

```text
<output_dir>/<pipeline name>/<version>/
  manifest.json
  DATASET_CARD.md
  data/<split>-<NNNNN>-of-<NNNNN>.parquet     (or .jsonl.zst)
  reports/
    report.md, summary.json, privacy.json, quality.json, validation.json
    removals.parquet, contamination.jsonl, ingest_errors.jsonl
    exact_duplicates.parquet, near_duplicate_clusters.parquet   (when duplicates exist)
    plots/*.svg
```

Shard names follow the Hugging Face convention, so
`datasets.load_dataset("parquet", data_files={"train": "data/train-*.parquet"})` works
unchanged. Shards are size-bounded by `shard.max_documents_per_shard` and
`shard.target_shard_bytes`. For Parquet the writer checks the compressed sink position after
every row group (`shard.row_group_documents`), so the target is met to within one row group
plus the footer. For zstd JSONL the target applies to uncompressed bytes. Compression is
`zstd` at level 3 by default (`shard.compression`, `shard.compression_level`). Every shard is
hashed with SHA-256 into the manifest.

## Storage backends

`StorageBackend` is the publishing port:

- `LocalStorage` publishes a staged dataset with a single atomic directory rename and
  refuses to overwrite an existing version (`DatasetExistsError`). Across filesystems it
  copies the files and writes `manifest.json` last.
- `FsspecStorage` targets any fsspec filesystem (`s3://` with the `s3` extra for AWS S3 or
  any S3-compatible server, `memory://` in tests). Object stores have no atomic rename, so it
  uploads all files first and `manifest.json` last: a dataset without a manifest is
  incomplete by definition. It also refuses to overwrite an existing manifest.

Computation always happens on local disk. In v0.1 the runner publishes to the local
`storage.output_dir`; `FsspecStorage` is implemented and tested but not yet selectable from
the pipeline configuration.

No object-storage server is bundled with the optional Compose stack. MinIO was the planned
local backend, but on 2026-11-13 its public images could not be pulled anonymously (Docker
Hub and quay.io both required authorization), and a pinned, unpullable service would only
break `docker compose`.

## Disk budget

Before a run, `estimate` compares the expected footprint with free space on the work
directory's filesystem minus `storage.reserve_free_gb` (5 GB by default):

- each materialization is assumed to need at most half the input size (zstd Parquet is
  usually smaller than raw text);
- with `cleanup: on_success` at most two materializations coexist; with `cleanup: never` all
  of them persist;
- the output is assumed to need one more copy.

If the estimate does not fit, the run refuses to start (`StorageBudgetExceeded`, exit code
4) unless `--ignore-disk-budget` is given. During the run, `DiskGuard` re-checks free space
before every checkpoint commit and stops at the last committed checkpoint when space runs
low, so the run can resume after space is freed.

## Cleanup

- `cleanup: on_success` (default): segment directories of the run are deleted after the
  dataset is published. Run records under `runs/` are kept.
- `cleanup: never`: intermediates are kept so unchanged segments become cache hits.
- `dataforge cache clean [--yes]` removes `segments/` and `staging/`.

## Serialization safety

DataForge persists data only as Parquet, JSON, JSON lines, NumPy arrays saved and loaded with
`allow_pickle=False`, and raw bit arrays (Bloom filter snapshots). It never unpickles data or
work files. YAML is parsed with `yaml.safe_load`.
