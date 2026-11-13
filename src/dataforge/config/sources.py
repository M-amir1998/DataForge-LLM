"""Typed source configuration (the source registry schema)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator

from dataforge.domain.document import UNKNOWN_LICENSE

SourceId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")]


class StrictModel(BaseModel):
    """Base for all configuration models: unknown keys are errors, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SourceBase(StrictModel):
    """Fields shared by every source type: governance metadata and record field mapping."""

    description: str = ""
    license: str = UNKNOWN_LICENSE
    license_source: str | None = Field(
        default=None,
        description="Where the license claim comes from (dataset card, LICENSE file, ...).",
    )
    usage_policy: str = "unspecified"
    version: str | None = Field(default=None, description="Human-readable source version label.")
    domain_hint: str | None = None
    language_hint: str | None = None
    content_kind: Literal["auto", "text", "code"] = "auto"
    max_records: PositiveInt | None = None
    tags: tuple[str, ...] = ()

    text_field: str = "text"
    id_field: str | None = None
    license_field: str | None = None
    url_field: str | None = None
    title_field: str | None = None
    timestamp_field: str | None = None
    metadata_fields: tuple[str, ...] = ()

    on_record_error: Literal["skip", "fail"] = "skip"
    max_record_errors: int = Field(default=100, ge=0)


class LocalSourceConfig(SourceBase):
    """Files on the local filesystem: plain text, JSONL, JSON, CSV, or Parquet."""

    type: Literal["local"]
    path: str
    format: Literal["auto", "text", "jsonl", "json", "csv", "parquet"] = "auto"
    include: tuple[str, ...] = ("**/*",)
    exclude: tuple[str, ...] = ()
    encoding: str = "auto"
    on_decode_error: Literal["replace", "skip", "fail"] = "replace"
    parser: Literal["auto", "plain", "markdown", "html", "code"] = "auto"
    max_file_bytes: PositiveInt = 64 * 1024 * 1024
    max_json_bytes: PositiveInt = 256 * 1024 * 1024
    csv_delimiter: Annotated[str, StringConstraints(min_length=1, max_length=1)] = ","


class CodeSourceConfig(SourceBase):
    """A local source-code tree: one document per file, with language from the extension."""

    type: Literal["code"]
    path: str
    include: tuple[str, ...] = ("**/*",)
    exclude: tuple[str, ...] = ()
    skip_dirs: tuple[str, ...] = (
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "vendor",
        "third_party",
        "dist",
        "build",
        "target",
    )
    max_file_bytes: PositiveInt = 1024 * 1024
    on_decode_error: Literal["replace", "skip", "fail"] = "skip"


class HuggingFaceSourceConfig(SourceBase):
    """A Hugging Face dataset read in streaming mode (requires the ``hf`` extra)."""

    type: Literal["huggingface"]
    dataset: str
    name: str | None = None
    split: str = "train"
    revision: str | None = Field(
        default=None, description="Pin a commit SHA or tag; unpinned sources are not reproducible."
    )
    streaming: bool = True


class SyntheticSourceConfig(SourceBase):
    """Deterministic generated text, used for demos and bounded-memory benchmarks."""

    type: Literal["synthetic"]
    num_documents: PositiveInt | None = None
    total_bytes: PositiveInt | None = None
    seed: int = 0
    mean_words: PositiveInt = 350

    @model_validator(mode="after")
    def _check_size(self) -> SyntheticSourceConfig:
        if (self.num_documents is None) == (self.total_bytes is None):
            raise ValueError("set exactly one of 'num_documents' or 'total_bytes'")
        return self


SourceConfig = Annotated[
    LocalSourceConfig | CodeSourceConfig | HuggingFaceSourceConfig | SyntheticSourceConfig,
    Field(discriminator="type"),
]


class SourceRegistryConfig(StrictModel):
    """A registry file: a named collection of sources."""

    version: Literal[1] = 1
    base_dir: str = Field(
        default=".",
        description="Directory (relative to this file) that relative paths resolve against.",
    )
    sources: dict[SourceId, SourceConfig]
