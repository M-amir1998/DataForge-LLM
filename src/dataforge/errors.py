"""Explicit error model.

Every failure mode the pipeline anticipates has its own type so that the CLI, Jenkins jobs
and library callers can react precisely instead of catching a bare ``Exception``. Messages
never embed document text: corpora may contain PII or secrets.
"""

from __future__ import annotations


class DataForgeError(Exception):
    """Base class for all anticipated DataForge failures."""

    #: Process exit code used by the CLI when this error terminates a command.
    exit_code: int = 1


class ConfigError(DataForgeError):
    """A configuration file is missing, malformed, or semantically invalid."""

    exit_code = 2


class OptionalDependencyMissing(DataForgeError):
    """A feature requires an optional extra that is not installed."""

    exit_code = 3

    def __init__(self, feature: str, extra: str) -> None:
        super().__init__(
            f"{feature} requires the optional '{extra}' extra: "
            f"install it with `uv sync --extra {extra}` or `pip install 'dataforge-llm[{extra}]'`."
        )
        self.feature = feature
        self.extra = extra


# --------------------------------------------------------------------------- sources


class SourceError(DataForgeError):
    """Base class for source acquisition and parsing failures."""


class SourceNotFound(SourceError):
    """A configured source or one of its files does not exist."""


class SourceFormatError(SourceError):
    """A source is corrupted beyond the configured error tolerance."""


class DecodingError(SourceError):
    """Bytes could not be decoded and the source is configured to fail on decode errors."""


class UnsafePathError(SourceError):
    """A path escapes its permitted root (path traversal, archive slip, symlink escape)."""


# --------------------------------------------------------------------------- storage


class StorageError(DataForgeError):
    """Base class for storage backend failures."""


class StorageBudgetExceeded(StorageError):
    """Free disk space would drop below the configured reserve."""

    exit_code = 4

    def __init__(self, message: str, *, required_bytes: int, available_bytes: int) -> None:
        super().__init__(message)
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes


# --------------------------------------------------------------------------- datasets


class InvalidManifest(DataForgeError):
    """A dataset manifest is missing, unparsable, or violates the manifest schema."""


class DatasetValidationError(DataForgeError):
    """A dataset failed integrity validation (hashes, counts, schema, leakage)."""

    exit_code = 5

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class DatasetExistsError(DataForgeError):
    """Refusing to overwrite an immutable, completed dataset version."""


class DuplicateDocumentId(DataForgeError):
    """Two distinct records produced the same document identifier."""


class ReproducibilityError(DataForgeError):
    """A reproduction run diverged from the recorded manifest."""

    exit_code = 6


# --------------------------------------------------------------------------- pipeline


class PipelineError(DataForgeError):
    """Base class for pipeline planning and execution failures."""


class PipelineStageFailure(PipelineError):
    """A stage raised an unexpected error; wraps the cause with stage context."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"stage '{stage}' failed: {message}")
        self.stage = stage


class UnsafeLicense(PipelineError):
    """A document's license violates a policy configured to fail rather than filter."""

    exit_code = 7


class ContaminationDetected(PipelineError):
    """Benchmark contamination was found and the policy is configured to fail."""

    exit_code = 8

    def __init__(self, message: str, matches: int) -> None:
        super().__init__(message)
        self.matches = matches
