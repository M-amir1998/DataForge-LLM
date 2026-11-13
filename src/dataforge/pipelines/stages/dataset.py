"""Dataset-construction stages: mixture, split, and token/length analysis."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from typing_extensions import override

from dataforge.config.models import AnalyzeConfig, MixtureConfig, SplitConfig
from dataforge.domain.document import Document
from dataforge.errors import PipelineError
from dataforge.mixtures.builder import MixtureBuilder
from dataforge.pipelines.stage import (
    BarrierStage,
    Emitter,
    Rejection,
    StageContext,
    StageResult,
    StreamingStage,
)
from dataforge.splitting.splitter import Splitter

_LENGTH_BINS = (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)


class MixtureStage(BarrierStage):
    name = "mixture"
    description = "Weighted domain mixture (document / byte / token units)"

    def __init__(self, config: MixtureConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.builder = MixtureBuilder(config, context.token_estimator)
        self.keep: npt.NDArray[np.bool_] | None = None

    @override
    def resources(self) -> dict[str, str]:
        return {"token_estimator": self.context.token_estimator.name}

    @override
    def reset(self, spill_dir: Path) -> None:
        self.builder.reset()
        self.keep = None

    @override
    def observe(self, documents: Sequence[Document]) -> None:
        self.builder.observe(documents)

    @override
    def finalize(self, decisions_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        keep, plan = self.builder.finalize()
        decisions_dir.mkdir(parents=True, exist_ok=True)
        MixtureBuilder.save(decisions_dir / f"{self.name}.npy", keep)
        summary = plan.as_dict()
        (decisions_dir / f"{self.name}.summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        self.keep = keep
        self.builder.reset()
        return summary, {}

    @override
    def load(self, decisions_dir: Path) -> dict[str, Any]:
        self.keep = MixtureBuilder.load(decisions_dir / f"{self.name}.npy")
        loaded: dict[str, Any] = json.loads((decisions_dir / f"{self.name}.summary.json").read_text(encoding="utf-8"))
        return loaded

    @override
    def apply(self, document: Document, ordinal: int, emit: Emitter) -> StageResult:
        if self.keep is None:
            raise PipelineError("mixture decisions are not loaded")
        if not bool(self.keep[ordinal]):
            return Rejection("mixture_not_sampled")
        emit.count(f"selected|{document.metadata.domain or 'general'}")
        return document


class SplitStage(StreamingStage):
    name = "split"
    description = "Deterministic group-aware split assignment"

    def __init__(self, config: SplitConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.splitter = Splitter(config)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        split = self.splitter.assign(document)
        document.metadata.split = split
        emit.count(f"split|{split}")
        return document


class TimeSplitStage(BarrierStage):
    """Time-based split: a group's earliest timestamp decides, so groups never straddle."""

    name = "split"
    description = "Time-based group-aware split assignment"

    def __init__(self, config: SplitConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.splitter = Splitter(config)

    @override
    def reset(self, spill_dir: Path) -> None:
        self.splitter = Splitter(self.cfg)

    @override
    def observe(self, documents: Sequence[Document]) -> None:
        self.splitter.observe(documents)

    @override
    def finalize(self, decisions_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        decisions_dir.mkdir(parents=True, exist_ok=True)
        groups = {key: value.isoformat() for key, value in sorted(self.splitter.group_times().items())}
        (decisions_dir / "split_groups.json").write_text(json.dumps(groups), encoding="utf-8")
        return {"groups_with_timestamps": len(groups)}, {}

    @override
    def load(self, decisions_dir: Path) -> dict[str, Any]:
        groups = json.loads((decisions_dir / "split_groups.json").read_text(encoding="utf-8"))
        self.splitter.set_group_times({key: dt.datetime.fromisoformat(value) for key, value in groups.items()})
        return {"groups_with_timestamps": len(groups)}

    @override
    def apply(self, document: Document, ordinal: int, emit: Emitter) -> StageResult:
        split = self.splitter.assign(document)
        document.metadata.split = split
        emit.count(f"split|{split}")
        return document


class AnalyzeStage(StreamingStage):
    name = "analyze"
    description = "Token and length analysis (token estimates on every document)"

    def __init__(self, config: AnalyzeConfig, context: StageContext) -> None:
        super().__init__(config, context)

    @override
    def resources(self) -> dict[str, str]:
        return {"token_estimator": self.context.token_estimator.name}

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        return self.process_batch([document], emit)[0]

    @override
    def process_batch(self, documents: Sequence[Document], emit: Emitter) -> list[StageResult]:
        estimates = self.context.token_estimator.estimate_batch([d.text for d in documents])
        for document, tokens in zip(documents, estimates, strict=True):
            document.metadata.token_estimate = tokens
            emit.count("tokens", tokens)
            split = document.metadata.split or "train"
            emit.count(f"tokens|{split}", tokens)
            chars = len(document.text)
            label = next((str(edge) for edge in _LENGTH_BINS if chars <= edge), f">{_LENGTH_BINS[-1]}")
            emit.count(f"length_bin|{label}")
        return list(documents)
