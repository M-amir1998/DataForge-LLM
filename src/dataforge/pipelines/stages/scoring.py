"""Scoring stages: heuristic quality, benchmark contamination, domain classification."""

from __future__ import annotations

import math
from collections.abc import Sequence

from typing_extensions import override

from dataforge.classification.domain import DomainClassifier
from dataforge.config.models import ClassifyConfig, ContaminationConfig, QualityConfig
from dataforge.contamination.detector import ContaminationDetector
from dataforge.contamination.registry import BenchmarkInfo, load_benchmarks
from dataforge.domain.document import ContaminationHit, Document
from dataforge.errors import ConfigError, ContaminationDetected
from dataforge.pipelines.stage import Emitter, Rejection, StageContext, StageResult, StreamingStage
from dataforge.quality.scorer import QualityScorer


class QualityStage(StreamingStage):
    name = "quality"
    description = "Heuristic quality signals combined by policy"

    def __init__(self, config: QualityConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.scorer = QualityScorer(config)

    @override
    def resources(self) -> dict[str, str]:
        learned = self.scorer.config.learned_model
        return {"learned_model": learned.entrypoint} if learned is not None else {}

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        return self.process_batch([document], emit)[0]

    @override
    def process_batch(self, documents: Sequence[Document], emit: Emitter) -> list[StageResult]:
        results: list[StageResult] = []
        for document, assessment in zip(documents, self.scorer.assess_batch(documents), strict=True):
            document.metadata.quality_score = assessment.score
            document.metadata.quality_signals = {
                name: value for name, value in assessment.values().items() if not math.isnan(value)
            }
            emit.count(f"score_bin|{min(9, int(assessment.score * 10))}")
            for signal in assessment.signals:
                if signal.applicable:
                    emit.count(f"evaluated|{signal.name}")
                    if not signal.passed:
                        emit.count(f"failed|{signal.name}")
            reason = assessment.rejection_reason
            results.append(Rejection(f"quality:{reason}") if reason else document)
        return results


class ContaminationStage(StreamingStage):
    name = "contamination"
    description = "Exact / substring / n-gram / MinHash overlap with protected benchmarks"

    def __init__(self, config: ContaminationConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        if config.registry is None:
            raise ConfigError("contamination stage needs a benchmark registry")
        registry_path = context.loaded.resolve(config.registry)
        probes, self.benchmarks = load_benchmarks(registry_path, config.benchmarks, config.ngram_size)
        self.detector = ContaminationDetector(probes, config)

    def benchmark_infos(self) -> list[BenchmarkInfo]:
        return list(self.benchmarks)

    @override
    def resources(self) -> dict[str, str]:
        return {f"benchmark:{b.name}": b.sha256 or "unavailable" for b in self.benchmarks}

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        matches = self.detector.scan(document.text)
        if not matches:
            return document
        document.metadata.contamination = [ContaminationHit(m.benchmark, m.item_id, m.method, m.score) for m in matches]
        emit.count("documents_contaminated")
        for match in matches:
            emit.count(f"match|{match.benchmark}|{match.method}")
            emit.artifact(
                "contamination_matches",
                {
                    "document_id": document.document_id,
                    "source_id": document.source_id,
                    "benchmark": match.benchmark,
                    "item_id": match.item_id,
                    "field": match.field,
                    "method": match.method,
                    "score": match.score,
                    "matched_ngrams": match.matched_ngrams,
                    "total_ngrams": match.total_ngrams,
                    "evidence_spans": [list(span) for span in match.spans],
                    "evidence_hash": match.evidence_hash,
                },
            )
        if self.cfg.policy == "fail":
            raise ContaminationDetected(
                f"document {document.document_id} overlaps benchmark '{matches[0].benchmark}' "
                f"(method={matches[0].method}); contamination policy is 'fail'",
                matches=len(matches),
            )
        if self.cfg.policy == "drop":
            return Rejection("contaminated")
        return document


class ClassifyStage(StreamingStage):
    name = "classify"
    description = "Domain classification (source hints, code, URL rules, keywords)"

    def __init__(self, config: ClassifyConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.classifier = DomainClassifier(config)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        source = self.context.source_config(document.source_id)
        prediction = self.classifier.classify(
            document.text,
            kind=document.metadata.content_kind,
            domain_hint=source.domain_hint if source is not None else None,
            url=document.metadata.url,
        )
        document.metadata.domain = prediction.domain
        document.metadata.domain_confidence = prediction.confidence
        emit.count(f"domain|{prediction.domain}")
        emit.count(f"method|{prediction.method}")
        return document
