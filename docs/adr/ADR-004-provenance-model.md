# ADR-004: Provenance model

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

A training dataset must answer, long after it was built: where did this document come from,
what happened to it, why was that other document removed, and can the dataset be rebuilt
exactly? Answers spread across logs and notebooks do not survive; they must travel with the
data.

## Decision

Provenance is recorded at three levels:

1. **Per document**, inside every shard: a stable `document_id` (hash of source, record key,
   raw content), `content_hash`, and a `provenance` struct with source URI, record locator,
   record id, source version, source file SHA-256, and the ordered list of text
   transformations. Governance metadata (license, license source, usage policy) and stage
   annotations sit in their own columns.
2. **Per removal**, in `reports/removals.parquet`: document id, source, record id, stage, reason,
   and bytes for every removed document, without its text.
3. **Per dataset**, in a schema-versioned `manifest.json`: sources with file-level SHA-256,
   benchmark files, code identity (version, Git SHA, dirty flag, code fingerprint), the
   embedded output-affecting configuration and its hash, shards with SHA-256, split totals,
   and stage statistics.

Details: [provenance](../governance/provenance.md).

## Consequences

- Any output row can be traced to an exact input record and the exact bytes of its source file.
- Per-row provenance costs storage, but repeated values (source URIs, versions, fingerprints,
  transformation tags) compress well under Parquet dictionary encoding.
- The manifest is enough to re-run and verify a dataset (`dataforge pipeline reproduce`).
- Removed documents remain explainable without retaining their content.
- Provenance for Hugging Face sources is only as strong as the pinned revision; unpinned
  sources are flagged as not reproducible.

## Alternatives considered

- **Dataset-level metadata only:** small, but cannot explain individual documents or removals.
- **External lineage service (for example OpenLineage):** valuable in a platform; the
  manifest's content could be emitted to such a service later. A file-based manifest keeps the
  local workflow self-contained.
- **Storing removed documents:** complete, but duplicates data that was removed for privacy,
  license, or quality reasons; the audit log keeps ids and reasons only.
