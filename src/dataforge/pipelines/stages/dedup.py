"""Barrier stages for exact and near-duplicate removal."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from typing_extensions import override

from dataforge.config.models import ExactDedupConfig, NearDedupConfig
from dataforge.dedup.exact import ExactDeduplicator, ExactDedupResult
from dataforge.dedup.near import NearDeduplicator, NearDedupResult
from dataforge.domain.document import Document
from dataforge.errors import PipelineError
from dataforge.pipelines.stage import BarrierStage, Emitter, Rejection, StageContext, StageResult


class ExactDedupStage(BarrierStage):
    name = "exact_dedup"
    description = "Document-level exact deduplication on normalized content hashes"

    def __init__(self, config: ExactDedupConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.dedup = ExactDeduplicator(config.policy)
        self.result: ExactDedupResult | None = None

    @override
    def reset(self, spill_dir: Path) -> None:
        self.dedup.reset()
        self.result = None

    @override
    def observe(self, documents: Sequence[Document]) -> None:
        self.dedup.observe(documents)

    @override
    def finalize(self, decisions_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        result, rows = self.dedup.finalize()
        decisions_dir.mkdir(parents=True, exist_ok=True)
        result.save(decisions_dir / f"{self.name}.npz")
        self.result = result
        self.dedup.reset()
        return result.summary(), {"exact_duplicates": rows}

    @override
    def load(self, decisions_dir: Path) -> dict[str, Any]:
        self.result = ExactDedupResult.load(decisions_dir / f"{self.name}.npz")
        return self.result.summary()

    @override
    def apply(self, document: Document, ordinal: int, emit: Emitter) -> StageResult:
        if self.result is None:
            raise PipelineError("exact_dedup decisions are not loaded")
        if bool(self.result.in_group[ordinal]):
            document.metadata.exact_dup_group = document.metadata.content_hash
        if not bool(self.result.keep[ordinal]):
            return Rejection("exact_duplicate")
        return document


class NearDedupStage(BarrierStage):
    name = "near_dedup"
    description = "MinHash/LSH near-duplicate clustering with deterministic representatives"

    def __init__(self, config: NearDedupConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.dedup: NearDeduplicator | None = None
        self.result: NearDedupResult | None = None

    @override
    def fingerprint_payload(self) -> dict[str, Any]:
        payload = super().fingerprint_payload()
        payload["max_bucket_size"] = self.cfg.max_bucket_size
        return payload

    @override
    def reset(self, spill_dir: Path) -> None:
        if self.dedup is not None:
            self.dedup.discard()
        self.dedup = NearDeduplicator(self.cfg, spill_dir)
        self.result = None

    @override
    def observe(self, documents: Sequence[Document]) -> None:
        if self.dedup is None:
            raise PipelineError("near_dedup.observe() before reset()")
        self.dedup.observe(documents)

    @override
    def finalize(self, decisions_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        if self.dedup is None:
            raise PipelineError("near_dedup.finalize() before reset()")
        result, rows = self.dedup.finalize()
        self.dedup.discard()
        decisions_dir.mkdir(parents=True, exist_ok=True)
        result.save(decisions_dir / f"{self.name}.npz")
        self.result = result
        return result.summary(), {"near_duplicate_clusters": rows}

    @override
    def load(self, decisions_dir: Path) -> dict[str, Any]:
        self.result = NearDedupResult.load(decisions_dir / f"{self.name}.npz")
        return self.result.summary()

    @override
    def apply(self, document: Document, ordinal: int, emit: Emitter) -> StageResult:
        result = self.result
        if result is None:
            raise PipelineError("near_dedup decisions are not loaded")
        cluster = result.cluster_ids[ordinal]
        if cluster:
            document.metadata.near_dup_cluster = cluster.decode("ascii")
            document.metadata.near_dup_similarity = round(float(result.similarity[ordinal]), 4)
        if not bool(result.keep[ordinal]):
            return Rejection("near_duplicate")
        return document
