# ADR-006: Immutable, content-addressed dataset versions

- **Status:** Accepted
- **Date:** 2026-11-13

## Context

Silently overwriting a dataset invalidates every experiment that used it. Hand-assigned
version labels drift from what the data actually contains. A dataset version must be a
precise statement: these inputs, this configuration, this code.

## Decision

- The dataset **version is a fingerprint**: the first twelve hex characters of a SHA-256 over
  the output-affecting config hash and the final segment fingerprint, which itself chains the
  source content fingerprints, the code fingerprint, and every stage's configuration and
  external resources. The full fingerprint is stored in the manifest.
- A dataset is assembled in a **staging** directory, **validated** (hashes, counts, schema, id
  uniqueness, split leakage, content-hash spot check), and only then **published** with an
  atomic rename to `<output_dir>/<name>/<version>/`.
- Publishing refuses to overwrite an existing version. Re-running an unchanged pipeline
  resolves to the existing version and does no work (status `exists`).
- Any change to inputs, output-affecting configuration, benchmark files, or code produces a new
  version; old versions are never modified. Execution and storage settings are excluded from
  the hash because they cannot change outputs.

## Consequences

- A version identifier is verifiable: `dataforge pipeline reproduce` rebuilds it and compares
  shard digests.
- Every code change creates new versions, even when outputs would be identical; presentation-
  only modules are excluded from the code fingerprint to limit that churn.
- Storage grows with every version; deletion of old versions is an explicit operator decision.
- Byte-identical rebuilds require the locked library versions.

## Alternatives considered

- **Semantic version labels in config:** human-friendly but unverifiable and easy to reuse by
  mistake. A human-readable `name` is kept; the version is derived.
- **Timestamped run directories:** never collide, but give no way to detect that two runs are
  the same dataset.
- **Mutable "latest" datasets:** convenient, and exactly the failure mode this ADR exists to
  prevent.
