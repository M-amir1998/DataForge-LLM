# Contributing

Thanks for helping improve DataForge-LLM. This guide covers the development setup, the checks
every change must pass, and the conventions the codebase follows.

## Setup

Requirements: Python 3.11+ (3.12 is pinned for development), [uv](https://docs.astral.sh/uv/),
and Git. No GPU is needed.

```bash
uv sync                      # creates .venv from uv.lock
uv run dataforge doctor      # environment check
uv run pre-commit install    # optional local hooks
```

## Checks

Run these before opening a pull request. CI runs the same checks, plus the smoke pipeline, the
package build, and dependency audits (see [docs/ci/jenkins.md](docs/ci/jenkins.md)):

```bash
uv run ruff format --check src tests scripts benchmarks experiments
uv run ruff check src tests scripts benchmarks experiments
uv run mypy                  # strict mode
uv run pytest                # unit, integration, property, end-to-end
uv run python scripts/scan_secrets.py
```

Useful variations:

- `uv run pytest tests/unit -q` for a fast loop; `-m "not slow"` skips the end-to-end suite.
- `HYPOTHESIS_PROFILE=ci uv run pytest tests/property` reproduces CI's derandomized
  property tests.
- Coverage must stay at or above 85% (`--cov`).

## Conventions

- **Typing:** the package is fully typed and checked with strict mypy. Public functions and
  classes carry concise docstrings; obvious code carries no comments.
- **Errors:** raise the specific type from `dataforge.errors` (each maps to a stable CLI exit
  code); never raise or catch bare `Exception` in library code.
- **No secrets or personal data in the repository.** Test data uses reserved values
  (example.com, fictional phone ranges, documentation IP ranges, payment-processor test
  numbers). Credential-shaped test values are assembled from fragments at runtime so no
  contiguous credential-like literal is committed.
- **Logging:** never log document text, PII, or credential values; log counts and types.
- **Determinism:** no iteration over sets where order affects output, no unseeded randomness,
  no wall-clock values in data outputs.
- **Commits:** small, logical commits with conventional prefixes (`feat`, `fix`, `test`,
  `docs`, `ci`, `build`, `refactor`). Do not commit corpora, generated datasets, caches,
  `.env` files, or large binaries.
- **CI:** Jenkins only. Do not add `.github/workflows/` (a test enforces this).

## Extending DataForge

### A new pipeline stage

1. Add a config model in `config/models.py` (subclass `StageConfig`), a field on
   `StagesConfig`, the name to `StageName`, and its position in `DEFAULT_STAGE_ORDER`.
2. Implement `StreamingStage` (per-document `process`, optional `process_batch`, report via the
   `Emitter`) or `BarrierStage` (`reset`, `observe`, `finalize`, `load`, `apply`) in
   `pipelines/stages/`. Return `Rejection(reason)` instead of dropping documents.
3. Register it in `STAGE_FACTORIES`; declare dependencies in `pipelines/graph.py` and its kind
   in `stage_kind` if it is a barrier.
4. Add unit tests for the algorithm and an integration test through `run_pipeline`.

### A new source type

Add a config model to `config/sources.py` (and to the `SourceConfig` union), implement
`SourceReader` (`describe` must fingerprint the exact content; `iter_records` must stream in a
deterministic order and yield `RecordError` for bad records), and register it in
`sources/registry.py`.

### A new quality signal

Subclass `Signal[YourConfig]` in `quality/signals.py`, set `name` and `applies_to`, add the config
model to `QualitySignalsConfig`, register it in the signal table, and add a test that shows it
passing and failing.

### A new parser

Implement the `Parser` protocol (`parse(content) -> ParsedText`) and register it for a MIME type
with `ParserRegistry.register`.

## Reporting security issues

Please do not open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).
