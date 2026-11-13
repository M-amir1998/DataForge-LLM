# ADR-002: Streaming, bounded-memory execution

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Pretraining corpora are routinely larger than the memory of the machine processing them.
DataForge must run on a 16 GB laptop, survive interruptions, and keep disk usage predictable
on a disk with tens of gigabytes free.

## Decision

- Documents flow in **batches** (`execution.batch_size`, 256 by default). No stage ever holds
  the corpus in memory.
- **Streaming stages** process one batch at a time and emit counters and artifact rows through
  a per-batch `Emitter`, so their memory is bounded by the batch.
- **Barrier stages** keep only compact per-document keys:
  - exact dedup: two 64-bit hash halves, length, quality, and a 32-byte id (about 60 bytes per
    document);
  - near dedup: length, quality, id, and a validity flag in memory; signatures (4 bytes per
    permutation per document) are appended to a spill file and memory-mapped at finalization;
  - mixture: a domain code, units, and a priority per document;
  - paragraph dedup: a Bloom filter whose size is fixed by configuration.
- Consecutive streaming stages are fused into **segments**; each barrier ends a segment, which
  is materialized once as checkpointed Parquet parts ([pipeline](../architecture/pipeline.md)).
- Sources stream: JSONL and CSV line by line with a per-record size cap, Parquet by batches,
  archives member by member. A JSON array source must fit `max_json_bytes`; larger ones must be
  converted to JSONL.
- DuckDB, used for validation and analytics, runs with an explicit memory limit and spills to
  disk.
- A disk budget check before the run and a disk guard before every checkpoint keep free space
  above a reserve.

## Consequences

- Peak memory is the batch, the part writer's row group buffer, and the barrier keys. The keys
  grow linearly with the number of documents (tens of bytes each), not with their size, which
  is comfortable at laptop scale (millions of documents) and the documented ceiling beyond it.
- The time-based split keeps one timestamp per duplicate group in a dictionary; this is the
  one barrier whose state is a Python mapping.
- Materializing once per barrier costs disk I/O and up to two compressed copies of the corpus
  (more with `cleanup: never`), in exchange for resumability and caching.
- Memory behaviour is measured, not assumed: see the
  [README measured results](../../README.md#measured-results).

## Alternatives considered

- **Load everything in memory:** simplest, but fails exactly on the corpora that matter.
- **Materialize after every stage:** maximal checkpoint granularity, but one full-corpus copy
  per stage on a disk-constrained machine.
- **No materialization (pure streaming end to end):** impossible for stages that must see the
  whole stream before deciding, unless decisions are recomputed in a second pass over the
  sources, which repeats parsing and normalization.
- **External sort / DuckDB for all barrier state:** the right answer when keys exceed memory;
  NumPy arrays are simpler and sufficient at the target scale.
