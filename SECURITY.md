# Security policy

DataForge-LLM processes untrusted data by design: crawled pages, third-party dumps, and source
code from unknown authors. This document describes the threat model, the mitigations in the
code, and how to report vulnerabilities.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's private vulnerability reporting: open the
repository's **Security** tab and choose **Report a vulnerability**. Please include a minimal
reproduction and the affected version. Do not open public issues or pull requests for
undisclosed vulnerabilities.

Supported versions: the latest release on the default branch.

## Threat model and mitigations

### Untrusted corpora

- Documents are treated as data only; nothing in a document is executed or evaluated.
- Readers enforce per-record and per-file size limits (`max_file_bytes`, `max_json_bytes`, and
  a 64 MiB cap per JSONL line and per CSV field), so a single oversized record cannot exhaust
  memory.
- Malformed records become structured, content-free `RecordError` entries; a source that
  exceeds `max_record_errors` stops the run instead of silently losing data.
- Log events and reports never include document text.

### Paths and archives

- File discovery is confined to the configured source root; symbolic links that resolve
  outside it raise `UnsafePathError`.
- **Archives (`.zip`, `.tar`, `.tar.gz`, `.tgz`) are streamed member by member and never
  extracted to disk**, which removes zip-slip and tar path-traversal attacks by construction.
  Member names are still validated (absolute paths, drive letters, and `..` components are
  rejected) because they become provenance locators.
- Symbolic links, hard links, devices, and FIFOs inside tar archives are skipped, never
  followed.
- **Decompression bombs** are bounded by limits on member size (64 MiB), total expanded size
  (4 GiB), member count (100,000), and, for zip members, the compression ratio (200:1). Member
  reads never trust declared sizes. Gzip-compressed text sources are read with the same
  per-file and per-record caps.

### Malicious or adversarial text

- Normalization removes control characters and zero-width spaces, replaces lone surrogates
  with U+FFFD, and normalizes line separators, so downstream tools never receive invalid
  UTF-8.
- Bidirectional control characters are not removed (they carry meaning in right-to-left text);
  treat rendered previews of untrusted text with care.
- Quality and contamination heuristics can be gamed by text crafted to pass them; they are
  filters, not security boundaries.

### Secrets and personal data in corpora

- The `secrets` and `pii` stages detect and, by default, redact credentials and common PII
  ([privacy](docs/governance/privacy.md)). Detection is pattern-based and incomplete.
- Detection results are reported as counts, types, and offsets only.
- `dataforge sources inspect` previews records with PII and credentials redacted.

### Secrets in this repository

- `.gitignore` excludes `.env` files, keys, credentials, corpora, and generated datasets.
- `scripts/scan_secrets.py` scans the working tree, staged changes (pre-commit hook), and the
  full Git history with DataForge's own detectors; CI also runs gitleaks when installed.
- Test credentials are assembled from fragments at runtime; no contiguous credential-shaped
  literal is committed.
- Jenkins credentials live only in the Jenkins credentials store ([Jenkins CI](docs/ci/jenkins.md)).

### Licenses

License metadata is declared, never inferred, and unknown licenses are rejected by default
([licensing](docs/governance/licensing.md)). This is a policy tool, not legal advice.

### Serialization

- DataForge never unpickles data or work files. Intermediate and output data are Parquet,
  JSON, and JSON lines; barrier decisions are NumPy arrays saved and loaded with
  `allow_pickle=False`; Bloom filter snapshots are raw bit arrays.
- YAML is parsed with `yaml.safe_load`.
- The optional `py3langid` adapter loads the language model bundled with that package through
  the package's own loader; install optional extras only from trusted package indexes.

### Code execution surfaces

- `stages.quality.learned_model.entrypoint` imports and calls `module:factory`. A pipeline
  configuration is therefore code: only run configurations you trust, and only reference
  modules you control.
- Hugging Face sources always pass `trust_remote_code=False`.
- `dataforge dataset sql` accepts exactly one `SELECT` statement (statements such as `COPY`,
  `ATTACH`, `INSTALL`, or writes are rejected) and disables DuckDB extension auto-install and
  auto-load. It can still read local files the user can read; it is a local analysis tool, not
  a sandbox for untrusted queries.

### Supply chain

- Dependencies are locked in `uv.lock`; CI checks that the lock matches `pyproject.toml` and
  audits the locked runtime dependencies with `pip-audit`.
- Container images run as non-root users.
