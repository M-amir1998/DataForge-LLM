# Pipeline execution model

## Stages

A pipeline is an ordered chain of stages, configured under `stages:` in the pipeline YAML.
Each stage has `enabled` and `mode` (`filter` or `annotate`).

| Stage | Kind | Default | What it does |
| :--- | :--- | :--- | :--- |
| `normalize` | streaming | on | Code detection, then Unicode / line-ending / whitespace normalization |
| `language` | streaming | on | Language identification, optional allowlist and confidence filter |
| `exact_dedup` | barrier | on | Removes exact duplicates of normalized content |
| `paragraph_dedup` | streaming, stateful | off | Removes paragraphs already seen earlier in the stream |
| `near_dedup` | barrier | on | MinHash/LSH near-duplicate clustering |
| `pii` | streaming | on | PII detection with detect / redact / drop |
| `secrets` | streaming | on | Credential detection with detect / redact / drop |
| `license` | streaming | on | License and usage-policy gate |
| `quality` | streaming | on | Fifteen heuristic quality signals |
| `contamination` | streaming | off | Overlap with protected benchmarks (needs a registry) |
| `classify` | streaming | on | Domain classification |
| `mixture` | barrier | off | Weighted domain mixture |
| `split` | streaming (`time` strategy: barrier) | on | Group-aware train / validation / test assignment |
| `analyze` | streaming | on | Token estimates and length histogram |

Ingestion always runs first and sharding always runs last; they are not configurable
stages.

### Default order

`normalize, language, exact_dedup, paragraph_dedup, near_dedup, pii, secrets, license,
quality, contamination, classify, mixture, split, analyze` follows the documented
pipeline sequence. Removing exact duplicates before near-duplicate clustering keeps the
clustering smaller; governance and quality run on deduplicated text; contamination and
classification see the final text; mixture, split, and analysis operate on the surviving
documents.

`pipeline.stage_order` overrides the order. A custom order must list every enabled stage,
and the graph rejects orders that violate a dependency:

| Stage | Requires (earlier in the order) |
| :--- | :--- |
| `exact_dedup`, `near_dedup` with `policy: keep_highest_quality` | `quality` |
| `mixture` | `classify` |
| `split` with `strategy: domain` | `classify` |

`dataforge pipeline plan CONFIG` prints the validated DAG and its segments without reading
data.

## Streaming and barrier stages

- A **streaming stage** decides one document at a time (`process`, or `process_batch` for
  vectorizable work). It reports counters and artifact rows through a per-batch `Emitter`
  instead of hidden state, so a batch's result depends only on the batch. A stage that is
  order-dependent declares `stateful = True` and saves a snapshot at every checkpoint
  (currently only `paragraph_dedup`, whose Bloom filter bits are persisted).
- A **barrier stage** needs the whole stream before deciding (deduplication, mixture,
  time-based split). It *observes* compact per-document keys while its input is being
  written, *finalizes* decisions once, persists them (NumPy `.npz`/`.npy` or JSON), and
  *applies* them by document ordinal while the next segment reads the data back.

A stage never drops documents itself; it returns a `Rejection(reason)`. In `filter` mode
the executor removes the document and appends a row to the removal audit log; in `annotate`
mode the document is kept and a flag `"<stage>:<reason>"` is appended to its `flags`
column. Text transformations (redaction, normalization) apply in both modes.

## Segments and materialization

Consecutive streaming stages are fused into a **segment** that reads its input once. Each
barrier ends a segment: its observation runs while the segment writes its output, and its
decisions are applied at the start of the next segment. With every stage enabled, as in
`configs/pipelines/smoke.yaml`, the plan is:

```text
segment 0: ingest -> normalize -> language -> observe:exact_dedup
segment 1: apply:exact_dedup -> paragraph_dedup -> observe:near_dedup
segment 2: apply:near_dedup -> pii -> secrets -> license -> quality -> contamination -> classify -> observe:mixture
segment 3: apply:mixture -> split -> analyze
```

Every segment writes its output as Parquet *parts* in the work directory; the last segment's
output is what gets sharded. The number of full-corpus materializations is therefore the
number of barriers plus one.

## Checkpoints and resume

Each segment directory, `<work_dir>/segments/seg<NN>-<fingerprint prefix>/`, contains
`parts/`, `artifacts/<name>/part-<NNNNN>.jsonl`, `state/` (stateful snapshots),
`decisions/` (barrier outputs), `spill/` (near-dedup signatures), `_progress.json`, and
`_SUCCESS` once complete.

A checkpoint is committed whenever the open part reaches `execution.part_max_documents`
documents, and at the end of the segment:

1. the disk guard checks free space against `storage.reserve_free_gb`;
2. the part's `.tmp` file is renamed to `part-<NNNNN>.parquet`;
3. the part's artifact rows (removals, detections, contamination matches, ingestion errors)
   and stateful snapshots are written;
4. `_progress.json` is atomically replaced with the new checkpoint appended (part name,
   document count, input cursor, per-stage statistics).

On restart with `--resume` (the default), anything not referenced by `_progress.json` is
deleted, stateful stages reload the snapshot of the last checkpoint, a barrier observing this
segment replays its observation from the committed parts, and reading resumes at the
recorded input cursor. For the ingestion segment the cursor counts raw source items
(records and record errors) in registry order, so resumption never re-emits anything.
Because streaming stages are pure per batch and barrier decisions are indexed by ordinal,
a resumed run produces the same shards as an uninterrupted one; an integration test crashes a
run mid-segment and checks the resumed shards byte for byte against a clean run.

A failure inside a stage is re-raised as `PipelineStageFailure` naming the stage; the
uncommitted part is discarded and the last checkpoint stays valid.

## Caching

Segment fingerprints form a chain: the first starts from the source fingerprints, and every
segment hashes the previous fingerprint, the code fingerprint, its layout, and each stage's
configuration, version, and external resources (benchmark file hashes, tokenizer name,
learned-model entrypoint). A completed segment whose fingerprint matches is reused (`segment
cache hit`); changing, for example, only `split.seed` re-runs the last segment alone.
Caching across runs requires `storage.cleanup: never`; with `on_success` (the default) the
intermediate segments are deleted after the dataset is published. `--no-cache` disables
reuse; `dataforge cache clean` deletes segments and staging areas.

## Dataset versions

The dataset version is the first twelve hex characters of a fingerprint over the
output-affecting config hash and the last segment fingerprint. Identical inputs, config, and
code always resolve to the same version; any change produces a new one. If the version
already exists, `pipeline run` returns status `exists` and does no work. See
[ADR-006](../adr/ADR-006-immutable-dataset-versions.md).

Execution settings (`execution.*`) and storage locations (`storage.*`) are excluded from
the config hash because they cannot change outputs.

## Partial runs

- `dataforge pipeline run CONFIG --until STAGE` stops after `STAGE` (no dataset is
  produced); a barrier `--until` finalizes and reports its decisions.
- `dataforge ingest CONFIG` runs ingestion only and prints per-source input statistics.
- `dataforge dedup exact|near`, `dataforge mixture build`, and `dataforge contamination scan`
  are convenience wrappers; `contamination scan` forces the stage into `annotate` mode so it
  reports overlap without removing anything.
- `--dry-run` prints sources, the DAG, the disk estimate, and the expected output location
  without reading data.

Every run records `<work_dir>/runs/<run_id>/run.json` (status, timings, segment outcomes,
stage statistics) and `events.jsonl` (structured logs); `dataforge report RUN_ID|latest`
summarizes it.
