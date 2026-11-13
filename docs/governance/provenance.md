# Provenance and lineage

Every output document can be traced to its source record, and every dataset version can be
traced to the configuration, code, and inputs that produced it.

## Document identity

- `document_id` is the xxh3-128 hash of the source id, the record key (the record's id field
  when configured, otherwise its locator), and the record's raw content. The same input
  record yields the same id on every run and machine; a change to the record's content yields
  a new id.
- `content_hash` is the xxh3-128 hash of the current text and is refreshed whenever a stage
  changes the text.

## Per-document provenance

The `provenance` column of every shard holds:

| Field | Example | Meaning |
| :--- | :--- | :--- |
| `source_uri` | `data/sample/raw/web.jsonl` | File path relative to the source's base directory, dataset URI, or generator URI |
| `record_locator` | `data/sample/raw/web.jsonl#L42`, `bundle.zip!a/b.txt`, `forum.csv#row=7` | Exact position of the record |
| `record_id` | value of the configured id field, or the file path | Stable record key |
| `source_version` | `sample-v1` | The registry's `version` label |
| `source_fingerprint` | SHA-256 of the source file | Ties the document to exact input bytes |
| `transformations` | `parse:html`, `normalize[nfc,trailing_ws]`, `paragraph_dedup[-1]`, `pii:redact[2]`, `secrets:redact[1]` | Every text-changing step, in order |

Annotations such as duplicate groups, clusters, quality signals, detections, contamination
matches, flags, and the split are stored in their own columns.

## Dataset manifest

`manifest.json` (schema `1.0`, validated on read) is the single source of truth about a
dataset version:

- identity: `dataset_id`, `name`, `version`, `fingerprint`, `created_at`, `run_id`;
- code: DataForge version, Git SHA, dirty flag, and code fingerprint;
- pipeline: config path, config hash, stage order, and the full output-affecting
  configuration embedded verbatim;
- tokenizer used for token estimates;
- sources: type, URI, declared license and evidence, usage policy, version, content
  fingerprint, every file with size and SHA-256, documents read, record errors, notes (for
  example an unpinned Hugging Face revision);
- benchmarks checked for contamination, with license, item counts, SHA-256, and availability;
- totals and per-split summaries; every shard with split, format, documents, bytes, tokens,
  and SHA-256;
- per-stage statistics (documents and bytes in and out, removal and flag reasons, time);
- reproduction hints.

A dataset is complete if and only if its manifest exists and validates.

## Lineage chain

```text
dataset version (fingerprint)
  -> output-affecting config hash
  -> code identity: DataForge version, Git SHA (+ dirty flag), code fingerprint
  -> source fingerprints: per-file SHA-256 (local, code), pinned revision (Hugging Face),
     generator parameters (synthetic)
  -> stage resources: benchmark file SHA-256, tokenizer, learned-model entrypoint
```

The **code fingerprint** is a SHA-256 over every Python source file of the installed
package, so it identifies the exact code even when DataForge is installed from a wheel
without Git. Presentation-only modules (`cli/`, `inspection.py`, `__main__.py`) are excluded
because they cannot change outputs; run options that can (`--until`, forced stage modes) are
part of stage fingerprints instead. The Git SHA comes from the repository containing the
package, or from the `DATAFORGE_GIT_SHA` environment variable when building images without
Git metadata.

## Reproduction

Run from the pipeline's base directory (usually the repository root):

```bash
dataforge pipeline reproduce <path to the dataset>/manifest.json [--config PATH] [--output-dir DIR]
```

The command:

1. loads the pipeline config recorded in the manifest (or `--config`) and requires its config
   hash to match the manifest (`ReproducibilityError`, exit code 6, otherwise);
2. re-fingerprints every source and refuses to continue if any differs from the manifest,
   naming the changed sources (`ReproducibilityError`, exit code 6);
3. warns when the current code fingerprint differs from the manifest's;
4. rebuilds the dataset from scratch, without cache or resume, into `--output-dir` (default
   `<work_dir>/reproduce/<version>/`);
5. compares every shard's SHA-256 with the manifest and reports `bit-identical` or the
   differing shards.

Byte-identical shards require the same locked library versions (`uv.lock`), because Parquet
writer output can change across PyArrow releases.

## Removal audit log

`reports/removals.parquet` has one row per removed document: `document_id`, `source_id`,
`record_id`, `stage`, `reason`, and `bytes`. It answers "why is my document not in the
dataset?" without keeping removed text. The report's "Removals by source" table aggregates it
per source and stage. Detection and contamination artifacts (`privacy.json`,
`contamination.jsonl`) and duplicate reports (`exact_duplicates.parquet`,
`near_duplicate_clusters.parquet`) complete the audit trail.

## Run records

Each run writes `<work_dir>/runs/<run_id>/run.json` (status, timings, peak memory, segment
fingerprints and cache hits, stage statistics) and `events.jsonl` (structured log events
with run id, stage, segment, documents, cursor, and memory).
