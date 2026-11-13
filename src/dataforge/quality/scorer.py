"""Combine quality signals into a score and a keep/reject decision.

``quality_score`` is the weighted fraction of applicable signals that pass: a transparent
heuristic summary in ``[0, 1]``, not a calibrated probability of human-judged quality.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dataforge.config.models import QualityConfig
from dataforge.domain.document import Document
from dataforge.quality.learned import LearnedQualityModel, load_model
from dataforge.quality.signals import QualitySignal, Signal, TextStats, build_signals


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    score: float
    passed: bool
    signals: tuple[QualitySignal, ...]
    failed: tuple[str, ...]

    @property
    def rejection_reason(self) -> str | None:
        if self.passed:
            return None
        return self.failed[0] if self.failed else "score_below_min"

    def values(self) -> dict[str, float]:
        return {s.name: round(s.value, 6) for s in self.signals if s.applicable}


class QualityScorer:
    def __init__(self, config: QualityConfig, model: LearnedQualityModel | None = None) -> None:
        self.config = config
        self.signals: list[Signal[Any]] = build_signals(config.signals)
        if model is None and config.learned_model is not None:
            model = load_model(config.learned_model)
        self.model = model

    def assess(self, document: Document, learned_probability: float | None = None) -> QualityAssessment:
        stats = TextStats(document.text)
        results = [signal.score(stats, document) for signal in self.signals]
        weights = {signal.name: signal.weight for signal in self.signals}
        if learned_probability is not None and self.config.learned_model is not None:
            threshold = self.config.learned_model.threshold
            results.append(QualitySignal("learned_model", learned_probability, learned_probability >= threshold))
            weights["learned_model"] = self.config.learned_model.weight
        applicable = [r for r in results if r.applicable]
        total_weight = sum(weights[r.name] for r in applicable)
        score = sum(weights[r.name] for r in applicable if r.passed) / total_weight if total_weight > 0 else 1.0
        failed = tuple(r.name for r in applicable if not r.passed)
        if self.config.combine == "all_required":
            passed = not failed
        else:
            passed = score >= self.config.min_score
            failed = () if passed else failed
        return QualityAssessment(round(score, 6), passed, tuple(results), failed)

    def assess_batch(self, documents: Sequence[Document]) -> list[QualityAssessment]:
        probabilities: Sequence[float | None]
        if self.model is not None:
            probabilities = [float(p) for p in self.model.predict([d.text for d in documents])]
        else:
            probabilities = [None] * len(documents)
        return [self.assess(doc, prob) for doc, prob in zip(documents, probabilities, strict=True)]
