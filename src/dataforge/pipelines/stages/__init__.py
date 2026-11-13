"""Concrete stage adapters and the factory that builds them from configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, TypeVar

from dataforge.config.models import (
    AnalyzeConfig,
    ClassifyConfig,
    ContaminationConfig,
    ExactDedupConfig,
    LanguageConfig,
    LicenseConfig,
    MixtureConfig,
    NearDedupConfig,
    NormalizeConfig,
    ParagraphDedupConfig,
    PIIConfig,
    QualityConfig,
    SecretsConfig,
    SplitConfig,
    StageConfig,
    StageName,
)
from dataforge.pipelines.stage import Stage, StageContext
from dataforge.pipelines.stages.dataset import (
    AnalyzeStage,
    MixtureStage,
    SplitStage,
    TimeSplitStage,
)
from dataforge.pipelines.stages.dedup import ExactDedupStage, NearDedupStage
from dataforge.pipelines.stages.governance import LicenseStage, PIIStage, SecretsStage
from dataforge.pipelines.stages.scoring import ClassifyStage, ContaminationStage, QualityStage
from dataforge.pipelines.stages.text import LanguageStage, NormalizeStage, ParagraphDedupStage

StageFactory = Callable[[StageConfig, StageContext], Stage]
ConfigT = TypeVar("ConfigT", bound=StageConfig)


def _typed(config_type: type[ConfigT], build: Callable[[ConfigT, StageContext], Stage]) -> StageFactory:
    def factory(config: StageConfig, context: StageContext) -> Stage:
        if not isinstance(config, config_type):
            raise TypeError(f"expected {config_type.__name__}, got {type(config).__name__}")
        return build(config, context)

    return factory


def _split(config: SplitConfig, context: StageContext) -> Stage:
    if config.strategy == "time":
        return TimeSplitStage(config, context)
    return SplitStage(config, context)


STAGE_FACTORIES: Final[dict[StageName, StageFactory]] = {
    "normalize": _typed(NormalizeConfig, NormalizeStage),
    "language": _typed(LanguageConfig, LanguageStage),
    "exact_dedup": _typed(ExactDedupConfig, ExactDedupStage),
    "paragraph_dedup": _typed(ParagraphDedupConfig, ParagraphDedupStage),
    "near_dedup": _typed(NearDedupConfig, NearDedupStage),
    "pii": _typed(PIIConfig, PIIStage),
    "secrets": _typed(SecretsConfig, SecretsStage),
    "license": _typed(LicenseConfig, LicenseStage),
    "quality": _typed(QualityConfig, QualityStage),
    "contamination": _typed(ContaminationConfig, ContaminationStage),
    "classify": _typed(ClassifyConfig, ClassifyStage),
    "mixture": _typed(MixtureConfig, MixtureStage),
    "split": _typed(SplitConfig, _split),
    "analyze": _typed(AnalyzeConfig, AnalyzeStage),
}


def build_stage(name: StageName, config: StageConfig, context: StageContext) -> Stage:
    return STAGE_FACTORIES[name](config, context)


__all__ = [
    "STAGE_FACTORIES",
    "AnalyzeStage",
    "ClassifyStage",
    "ContaminationStage",
    "ExactDedupStage",
    "LanguageStage",
    "LicenseStage",
    "MixtureStage",
    "NearDedupStage",
    "NormalizeStage",
    "PIIStage",
    "ParagraphDedupStage",
    "QualityStage",
    "SecretsStage",
    "SplitStage",
    "TimeSplitStage",
    "build_stage",
]
