# Scale-out design

DataForge v0.1 runs in a single process on one machine. Nothing in this document is
implemented yet; it records how the current design maps onto distributed execution so that
scaling up does not require redesigning the data model. The local implementation stays the
reference: distributed runs must produce the same manifests and, given the same code and
library versions, the same shards.

## What already scales

- **Streaming stages are per-batch pure functions.** A stage's output and its counters and
  artifact rows (the `Emitter`) depend only on the batch and the stage configuration. They can
  run in a process pool, Ray tasks, or Spark `mapPartitions` without coordination, as long as
  results are written back in input order (or ordinals are carried explicitly).
- **Barrier stages keep compact keys, not documents.** Exact dedup needs a 128-bit content hash,
  length, quality, and id per document; near dedup needs a MinHash signature and a few scalars;
  the mixture needs domain, units, and a priority hash. These are the only data that must be
  shuffled.
- **Identity is content-derived.** Document ids, content hashes, cluster ids, split
  assignment, and mixture priority are all hashes of document content or keys, so they are
  stable regardless of partitioning.

## Mapping stages to workers

| Stage family | Distribution strategy |
| :--- | :--- |
| Ingestion | One task per source file or archive member range; Hugging Face streaming split by shard |
| Streaming stages | Fused per partition, exactly as segments are fused locally |
| Exact dedup | Shuffle by content-hash prefix; each partition groups and picks representatives with the same policy ordering |
| Near dedup | Compute signatures per partition; for each LSH band, shuffle `(band hash, document)` by band hash; verify candidate pairs in the reducers; merge clusters with a distributed connected-components pass (or a union-find over the comparatively small edge list on the driver) |
| Paragraph dedup | Replace the single Bloom filter with a partitioned, sharded filter keyed by paragraph hash, or a two-pass "first occurrence" join; this stage is order-dependent and needs a defined global order |
| Mixture | Aggregate per-domain units (a small reduce), compute the allocation on the driver, then select by the same seeded hash threshold per document |
| Split | Already map-only for hash, source, and domain strategies; time strategy needs a group-minimum reduce |
| Sharding | One writer per (split, output partition); shard names become `<split>-<partition>-<index>` and the manifest lists them |

## Storage

Object storage (AWS S3 or any S3-compatible server) replaces the local work directory for
parts and the output directory for datasets. `FsspecStorage` already
implements the manifest-last publishing protocol that object stores need, because they lack
atomic directory renames. Workers would write parts to unique keys and commit by writing a
small marker or progress object last, mirroring the local checkpoint protocol.

## What stays the same

- The canonical schema and its version metadata.
- Manifests: shard SHA-256 digests, source fingerprints, code identity, embedded config.
- Fingerprints and dataset versions: content-addressed, immutable.
- Validation: DuckDB can read Parquet directly from object storage through its `httpfs`
  extension; the checks themselves are unchanged.

## Incremental processing

Segment fingerprints are already chained per pipeline. A future step is to fingerprint per
source: if one source changes, only that source's ingestion and streaming work needs to be
recomputed, while barrier stages re-run over the union (they are cheap relative to text
processing because they operate on compact keys). This requires making ingestion and the
first segment source-partitioned, which v0.1 deliberately does not do to keep resume logic
simple.

## Framework choice

Ray fits per-batch Python stages and keeps the current code largely unchanged; Spark fits
very large shuffles (exact and near dedup at billions of documents) but would need stage
logic wrapped as UDFs. Neither is a dependency in v0.1, and the local path must keep working
without them.
