"""Typed pipeline configuration.

Every knob that changes pipeline output lives in a validated model; invalid values fail at
load time, before any data is read.
"""

from __future__ import annotations

import math
from typing import Annotated, Final, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, StringConstraints, model_validator

from dataforge.config.sources import SourceConfig, SourceId, StrictModel

StageName = Literal[
    "normalize",
    "language",
    "exact_dedup",
    "paragraph_dedup",
    "near_dedup",
    "pii",
    "secrets",
    "license",
    "quality",
    "contamination",
    "classify",
    "mixture",
    "split",
    "analyze",
]

#: Canonical stage order (mirrors the documented pipeline; override with ``stage_order``).
DEFAULT_STAGE_ORDER: Final[tuple[StageName, ...]] = (
    "normalize",
    "language",
    "exact_dedup",
    "paragraph_dedup",
    "near_dedup",
    "pii",
    "secrets",
    "license",
    "quality",
    "contamination",
    "classify",
    "mixture",
    "split",
    "analyze",
)

DatasetName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
SplitName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
DuplicatePolicy = Literal["keep_first", "keep_longest", "keep_highest_quality"]
StageMode = Literal["filter", "annotate"]

_SUM_TOLERANCE: Final = 1e-6


def _check_distribution(values: dict[str, float], what: str) -> None:
    if not values:
        raise ValueError(f"{what} must not be empty")
    if any(value < 0 or math.isnan(value) for value in values.values()):
        raise ValueError(f"{what} must be non-negative")
    total = sum(values.values())
    if abs(total - 1.0) > _SUM_TOLERANCE:
        raise ValueError(f"{what} must sum to 1.0 (got {total:.6f})")


class StageConfig(StrictModel):
    """Fields common to all stages."""

    enabled: bool = True
    mode: StageMode = Field(
        default="filter",
        description="'filter' removes rejected documents; 'annotate' keeps them and records a flag.",
    )


# --------------------------------------------------------------------------- stages


class NormalizeConfig(StageConfig):
    unicode_form: Literal["NFC", "NFKC", "none"] = "NFC"
    strip_control_chars: bool = True
    remove_zero_width: bool = True
    normalize_line_endings: bool = True
    normalize_spaces: bool = Field(
        default=True, description="Map exotic Unicode spaces (NBSP, en/em spaces) to ASCII space."
    )
    strip_trailing_whitespace: bool = True
    max_consecutive_blank_lines: PositiveInt | None = 2
    trim_document: bool = True
    detect_code: bool = True
    code_threshold: Fraction = 0.5
    min_chars: NonNegativeInt = 1


class LanguageConfig(StageConfig):
    detector: Literal["builtin", "py3langid"] = "builtin"
    allowed: tuple[str, ...] = ()
    min_confidence: Fraction = 0.0
    skip_code: bool = True
    sample_chars: PositiveInt = 5000


class ExactDedupConfig(StageConfig):
    policy: DuplicatePolicy = "keep_first"


class ParagraphDedupConfig(StageConfig):
    enabled: bool = False
    min_chars: PositiveInt = 40
    capacity: PositiveInt = 10_000_000
    error_rate: Annotated[float, Field(gt=0.0, lt=0.5)] = 0.001
    apply_to: tuple[Literal["text", "code", "mixed"], ...] = ("text",)


class NearDedupConfig(StageConfig):
    shingle_size: PositiveInt = 5
    num_perm: Annotated[int, Field(ge=16, le=1024)] = 128
    threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.85
    bands: PositiveInt | None = None
    rows: PositiveInt | None = None
    policy: DuplicatePolicy = "keep_longest"
    seed: int = 42
    max_bucket_size: PositiveInt = 500

    @model_validator(mode="after")
    def _check_banding(self) -> NearDedupConfig:
        if (self.bands is None) != (self.rows is None):
            raise ValueError("set both 'bands' and 'rows', or neither (auto-tuned)")
        if self.bands is not None and self.rows is not None and self.bands * self.rows > self.num_perm:
            raise ValueError("bands * rows must not exceed num_perm")
        return self


PIIType = Literal["email", "phone", "ip_address", "credit_card", "us_ssn", "iban"]
ALL_PII_TYPES: Final[tuple[PIIType, ...]] = (
    "email",
    "phone",
    "ip_address",
    "credit_card",
    "us_ssn",
    "iban",
)


class PIIConfig(StageConfig):
    policy: Literal["detect", "redact", "drop"] = "redact"
    detectors: tuple[PIIType, ...] = ALL_PII_TYPES
    drop_threshold: PositiveInt = 1


SecretType = Literal[
    "private_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "github_token",
    "gitlab_token",
    "slack_token",
    "stripe_secret_key",
    "google_api_key",
    "huggingface_token",
    "openai_api_key",
    "jwt",
    "password_assignment",
    "high_entropy_assignment",
]
ALL_SECRET_TYPES: Final[tuple[SecretType, ...]] = (
    "private_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "github_token",
    "gitlab_token",
    "slack_token",
    "stripe_secret_key",
    "google_api_key",
    "huggingface_token",
    "openai_api_key",
    "jwt",
    "password_assignment",
    "high_entropy_assignment",
)


class SecretsConfig(StageConfig):
    policy: Literal["detect", "redact", "drop"] = "redact"
    detectors: tuple[SecretType, ...] = ALL_SECRET_TYPES
    min_entropy: Annotated[float, Field(ge=0.0, le=8.0)] = 3.5
    min_length: PositiveInt = 12


class LicenseConfig(StageConfig):
    allowed: tuple[str, ...] = (
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "Unlicense",
        "LicenseRef-PublicDomain",
    )
    denied: tuple[str, ...] = ()
    unknown_policy: Literal["reject", "allow", "fail"] = "reject"
    disallowed_policy: Literal["reject", "fail"] = "reject"
    allowed_usage_policies: tuple[str, ...] | None = None


# --------------------------------------------------------------------------- quality


class SignalConfig(StrictModel):
    enabled: bool = True
    weight: Annotated[float, Field(ge=0.0)] = 1.0


class LengthSignalConfig(SignalConfig):
    min_chars: NonNegativeInt = 200
    max_chars: PositiveInt = 1_000_000
    min_words: NonNegativeInt = 30


class MeanWordLengthSignalConfig(SignalConfig):
    min: Annotated[float, Field(ge=0.0)] = 3.0
    max: Annotated[float, Field(gt=0.0)] = 10.0


class AlphaWordsSignalConfig(SignalConfig):
    min_ratio: Fraction = 0.8


class SymbolRatioSignalConfig(SignalConfig):
    max_ratio: Annotated[float, Field(ge=0.0)] = 0.1


class LineRepetitionSignalConfig(SignalConfig):
    max_duplicate_line_fraction: Fraction = 0.3
    max_duplicate_char_fraction: Fraction = 0.2


class NgramRepetitionSignalConfig(SignalConfig):
    n: Annotated[int, Field(ge=1, le=10)] = 3
    max_ratio: Fraction = 0.35


class CharEntropySignalConfig(SignalConfig):
    min_bits: Annotated[float, Field(ge=0.0)] = 3.0
    min_chars: NonNegativeInt = 50


class UrlRatioSignalConfig(SignalConfig):
    max_ratio: Fraction = 0.2


class BoilerplateSignalConfig(SignalConfig):
    max_line_fraction: Fraction = 0.3
    extra_patterns: tuple[str, ...] = ()


class TerminalPunctuationSignalConfig(SignalConfig):
    min_fraction: Fraction = 0.3
    min_line_words: PositiveInt = 3


class StopwordSignalConfig(SignalConfig):
    min_stopwords: NonNegativeInt = 2
    languages: tuple[str, ...] = ("en",)


class LanguageConfidenceSignalConfig(SignalConfig):
    min_confidence: Fraction = 0.5


class SentenceLengthSignalConfig(SignalConfig):
    min_mean_words: Annotated[float, Field(ge=0.0)] = 3.0
    max_mean_words: Annotated[float, Field(gt=0.0)] = 80.0


class CodeLineLengthSignalConfig(SignalConfig):
    max_line_length: PositiveInt = 1000
    max_mean_line_length: PositiveInt = 100


class AlnumRatioSignalConfig(SignalConfig):
    min_ratio: Fraction = 0.25


class QualitySignalsConfig(StrictModel):
    length: LengthSignalConfig = LengthSignalConfig()
    mean_word_length: MeanWordLengthSignalConfig = MeanWordLengthSignalConfig()
    alpha_words: AlphaWordsSignalConfig = AlphaWordsSignalConfig()
    symbol_ratio: SymbolRatioSignalConfig = SymbolRatioSignalConfig()
    line_repetition: LineRepetitionSignalConfig = LineRepetitionSignalConfig()
    ngram_repetition: NgramRepetitionSignalConfig = NgramRepetitionSignalConfig()
    char_entropy: CharEntropySignalConfig = CharEntropySignalConfig()
    url_ratio: UrlRatioSignalConfig = UrlRatioSignalConfig()
    boilerplate: BoilerplateSignalConfig = BoilerplateSignalConfig()
    terminal_punctuation: TerminalPunctuationSignalConfig = TerminalPunctuationSignalConfig()
    stopwords: StopwordSignalConfig = StopwordSignalConfig()
    language_confidence: LanguageConfidenceSignalConfig = LanguageConfidenceSignalConfig()
    sentence_length: SentenceLengthSignalConfig = SentenceLengthSignalConfig()
    code_line_length: CodeLineLengthSignalConfig = CodeLineLengthSignalConfig()
    alnum_ratio: AlnumRatioSignalConfig = AlnumRatioSignalConfig()


class LearnedQualityModelConfig(StrictModel):
    """Plug-in point for a learned quality classifier (see ``dataforge.quality.learned``)."""

    entrypoint: Annotated[str, StringConstraints(pattern=r"^[\w.]+:[\w]+$")]
    threshold: Fraction = 0.5
    weight: Annotated[float, Field(ge=0.0)] = 1.0


class QualityConfig(StageConfig):
    combine: Literal["all_required", "min_score"] = "all_required"
    min_score: Fraction = 0.0
    signals: QualitySignalsConfig = QualitySignalsConfig()
    learned_model: LearnedQualityModelConfig | None = None


# --------------------------------------------------------------------------- contamination

ContaminationMethod = Literal["exact", "substring", "ngram", "minhash"]


class ContaminationConfig(StageConfig):
    registry: str | None = None
    benchmarks: tuple[str, ...] = ()
    methods: tuple[ContaminationMethod, ...] = ("exact", "substring", "ngram", "minhash")
    ngram_size: Annotated[int, Field(ge=3, le=50)] = 8
    ngram_threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5
    minhash_threshold: Annotated[float, Field(gt=0.0, le=1.0)] = 0.8
    minhash_num_perm: Annotated[int, Field(ge=16, le=1024)] = 128
    shingle_size: PositiveInt = 5
    policy: Literal["flag", "drop", "fail"] = "drop"
    max_evidence_spans: PositiveInt = 5

    @model_validator(mode="after")
    def _check_registry(self) -> ContaminationConfig:
        if self.enabled and self.registry is None:
            raise ValueError("an enabled contamination stage needs a benchmark 'registry'")
        return self


# --------------------------------------------------------------------------- classification


class ClassifyConfig(StageConfig):
    use_source_hints: bool = True
    use_url_rules: bool = True
    min_score: Annotated[float, Field(ge=0.0)] = 1.0
    fallback_domain: str = "general"
    extra_keywords: dict[str, tuple[str, ...]] = Field(default_factory=dict)


# --------------------------------------------------------------------------- mixture / split


class MixtureConfig(StageConfig):
    enabled: bool = False
    unit: Literal["documents", "bytes", "tokens"] = "tokens"
    weights: dict[str, float] = Field(default_factory=dict)
    target_total: PositiveInt | None = None
    unlisted_domains: Literal["drop", "keep"] = "drop"
    seed: int = 0

    @model_validator(mode="after")
    def _check_weights(self) -> MixtureConfig:
        if self.enabled:
            _check_distribution(self.weights, "mixture weights")
        return self


class SplitConfig(StageConfig):
    strategy: Literal["hash", "source", "domain", "time"] = "hash"
    ratios: dict[SplitName, float] = Field(default_factory=lambda: {"train": 0.98, "validation": 0.01, "test": 0.01})
    seed: int = 0
    assignments: dict[str, SplitName] = Field(
        default_factory=dict, description="Explicit source/domain -> split overrides."
    )
    time_cutoffs: dict[SplitName, str] = Field(
        default_factory=dict,
        description="Ordered split -> ISO-8601 lower bound; earlier documents fall in the first split.",
    )
    missing_timestamp_split: SplitName = "train"

    @model_validator(mode="after")
    def _check_ratios(self) -> SplitConfig:
        _check_distribution(dict(self.ratios), "split ratios")
        for split in self.assignments.values():
            if split not in self.ratios:
                raise ValueError(f"assignment target '{split}' is not a configured split")
        if self.strategy == "time":
            if not self.time_cutoffs:
                raise ValueError("strategy 'time' needs 'time_cutoffs'")
            unknown = set(self.time_cutoffs) - set(self.ratios)
            if unknown:
                raise ValueError(f"time_cutoffs reference unknown splits: {sorted(unknown)}")
        return self


class AnalyzeConfig(StageConfig):
    """Token / length analysis; annotates token estimates for every surviving document."""


class StagesConfig(StrictModel):
    normalize: NormalizeConfig = NormalizeConfig()
    language: LanguageConfig = LanguageConfig()
    exact_dedup: ExactDedupConfig = ExactDedupConfig()
    paragraph_dedup: ParagraphDedupConfig = ParagraphDedupConfig()
    near_dedup: NearDedupConfig = NearDedupConfig()
    pii: PIIConfig = PIIConfig()
    secrets: SecretsConfig = SecretsConfig()
    license: LicenseConfig = LicenseConfig()
    quality: QualityConfig = QualityConfig()
    contamination: ContaminationConfig = ContaminationConfig(enabled=False)
    classify: ClassifyConfig = ClassifyConfig()
    mixture: MixtureConfig = MixtureConfig()
    split: SplitConfig = SplitConfig()
    analyze: AnalyzeConfig = AnalyzeConfig()

    def get(self, name: StageName) -> StageConfig:
        stage: StageConfig = getattr(self, name)
        return stage


# --------------------------------------------------------------------------- top level


class TokenizerConfig(StrictModel):
    kind: Literal["chars", "words", "hf"] = "chars"
    chars_per_token: Annotated[float, Field(gt=0.0)] = 4.0
    tokens_per_word: Annotated[float, Field(gt=0.0)] = 1.3
    tokenizer_path: str | None = None

    @model_validator(mode="after")
    def _check_hf(self) -> TokenizerConfig:
        if self.kind == "hf" and not self.tokenizer_path:
            raise ValueError("kind 'hf' needs 'tokenizer_path' (a local tokenizer.json)")
        return self


class ShardConfig(StrictModel):
    format: Literal["parquet", "jsonl"] = "parquet"
    compression: Literal["zstd", "snappy", "gzip", "none"] = "zstd"
    compression_level: Annotated[int, Field(ge=1, le=22)] | None = 3
    max_documents_per_shard: PositiveInt = 50_000
    target_shard_bytes: PositiveInt = 128 * 1024 * 1024
    row_group_documents: PositiveInt = 2_000


class ExecutionConfig(StrictModel):
    """Execution knobs. These never change outputs, so they are excluded from the config hash."""

    batch_size: PositiveInt = 256
    part_max_documents: PositiveInt = 20_000
    duckdb_memory_limit_mb: PositiveInt = 1024
    duckdb_threads: PositiveInt = 2


class StorageConfig(StrictModel):
    """Where work and outputs go. Excluded from the config hash (does not change content)."""

    work_dir: str = ".dataforge/work"
    output_dir: str = ".dataforge/datasets"
    reserve_free_gb: Annotated[float, Field(ge=0.0)] = 5.0
    cleanup: Literal["on_success", "never"] = "on_success"
    intermediate_compression: Literal["zstd", "lz4", "snappy", "none"] = "zstd"


class SourcesSection(StrictModel):
    registry: str | None = None
    include: tuple[SourceId, ...] = ()
    inline: dict[SourceId, SourceConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_any(self) -> SourcesSection:
        if self.registry is None and not self.inline:
            raise ValueError("configure a source 'registry' file and/or 'inline' sources")
        return self


class PipelineSection(StrictModel):
    name: DatasetName
    description: str = ""
    stage_order: tuple[StageName, ...] | None = None

    @model_validator(mode="after")
    def _check_order(self) -> PipelineSection:
        if self.stage_order is not None and len(set(self.stage_order)) != len(self.stage_order):
            raise ValueError("stage_order contains duplicate stages")
        return self


class PipelineConfig(StrictModel):
    version: Literal[1] = 1
    base_dir: str = Field(
        default=".",
        description="Directory (relative to this file) that relative paths resolve against.",
    )
    pipeline: PipelineSection
    sources: SourcesSection
    execution: ExecutionConfig = ExecutionConfig()
    storage: StorageConfig = StorageConfig()
    tokenizer: TokenizerConfig = TokenizerConfig()
    stages: StagesConfig = StagesConfig()
    shard: ShardConfig = ShardConfig()

    def stage_order(self) -> tuple[StageName, ...]:
        return self.pipeline.stage_order or DEFAULT_STAGE_ORDER


__all__ = [
    "ALL_PII_TYPES",
    "ALL_SECRET_TYPES",
    "DEFAULT_STAGE_ORDER",
    "AnalyzeConfig",
    "ClassifyConfig",
    "ContaminationConfig",
    "ContaminationMethod",
    "DuplicatePolicy",
    "ExactDedupConfig",
    "ExecutionConfig",
    "LanguageConfig",
    "LearnedQualityModelConfig",
    "LicenseConfig",
    "MixtureConfig",
    "NearDedupConfig",
    "NormalizeConfig",
    "PIIConfig",
    "PIIType",
    "ParagraphDedupConfig",
    "PipelineConfig",
    "PipelineSection",
    "QualityConfig",
    "QualitySignalsConfig",
    "SecretType",
    "SecretsConfig",
    "ShardConfig",
    "SourceConfig",
    "SourceId",
    "SourcesSection",
    "SplitConfig",
    "StageConfig",
    "StageMode",
    "StageName",
    "StagesConfig",
    "StorageConfig",
    "TokenizerConfig",
]
