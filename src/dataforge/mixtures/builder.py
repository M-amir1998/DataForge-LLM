"""Domain mixture construction.

Given per-domain availability ``A_d`` (in the configured unit) and target weights ``w_d``,
the builder computes the largest total ``T`` that honours the weights without upsampling,
``T = min_d A_d / w_d`` (or the configured ``target_total`` when smaller), and selects
``t_d = w_d * T`` units per domain. Documents are taken in order of a seeded hash of their
id, so selection is deterministic, independent of input order, and stable when unrelated
documents are added.

Units matter: sampling by *documents* over-represents domains with short documents when
measured in tokens; *bytes* and *tokens* track training compute more closely; *tokens*
depends on the configured estimator.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dataforge.config.models import MixtureConfig
from dataforge.domain.document import Document
from dataforge.domain.hashing import unit_interval
from dataforge.tokenization.estimators import TokenEstimator


@dataclass(frozen=True, slots=True)
class DomainAllocation:
    domain: str
    weight: float
    available_documents: int
    available_units: int
    target_units: float
    selected_documents: int
    selected_units: int

    @property
    def sampling_rate(self) -> float:
        return self.selected_units / self.available_units if self.available_units else 0.0


@dataclass(frozen=True, slots=True)
class MixturePlan:
    unit: str
    total_target: float
    allocations: tuple[DomainAllocation, ...]
    unlisted_policy: str
    unlisted_documents: int
    notes: tuple[str, ...]

    def achieved_weights(self) -> dict[str, float]:
        selected = sum(a.selected_units for a in self.allocations)
        return {a.domain: (a.selected_units / selected if selected else 0.0) for a in self.allocations}

    def as_dict(self) -> dict[str, Any]:
        achieved = self.achieved_weights()
        return {
            "unit": self.unit,
            "total_target": self.total_target,
            "unlisted_policy": self.unlisted_policy,
            "unlisted_documents": self.unlisted_documents,
            "notes": list(self.notes),
            "domains": [
                {
                    "domain": a.domain,
                    "target_weight": a.weight,
                    "achieved_weight": round(achieved[a.domain], 6),
                    "available_documents": a.available_documents,
                    "available_units": a.available_units,
                    "target_units": round(a.target_units, 2),
                    "selected_documents": a.selected_documents,
                    "selected_units": a.selected_units,
                    "sampling_rate": round(a.sampling_rate, 6),
                }
                for a in self.allocations
            ],
        }


class MixtureBuilder:
    def __init__(self, config: MixtureConfig, estimator: TokenEstimator) -> None:
        self.config = config
        self.estimator = estimator
        self.reset()

    def reset(self) -> None:
        self._domains: list[str] = []
        self._domain_index: dict[str, int] = {}
        self._codes: list[npt.NDArray[np.int32]] = []
        self._units: list[npt.NDArray[np.int64]] = []
        self._priority: list[npt.NDArray[np.float64]] = []

    def units(self, document: Document) -> int:
        unit = self.config.unit
        if unit == "documents":
            return 1
        if unit == "bytes":
            return document.num_bytes
        if document.metadata.token_estimate is None:
            document.metadata.token_estimate = self.estimator.estimate(document.text)
        return document.metadata.token_estimate

    def _code(self, domain: str) -> int:
        if domain not in self._domain_index:
            self._domain_index[domain] = len(self._domains)
            self._domains.append(domain)
        return self._domain_index[domain]

    def observe(self, documents: Sequence[Document]) -> None:
        if not documents:
            return
        salt = f"mixture:{self.config.seed}"
        self._codes.append(np.array([self._code(d.metadata.domain or "general") for d in documents], dtype=np.int32))
        self._units.append(np.array([self.units(d) for d in documents], dtype=np.int64))
        self._priority.append(np.array([unit_interval(d.document_id, salt) for d in documents], dtype=np.float64))

    def finalize(self) -> tuple[npt.NDArray[np.bool_], MixturePlan]:
        cfg = self.config
        codes = np.concatenate(self._codes) if self._codes else np.empty(0, dtype=np.int32)
        units = np.concatenate(self._units) if self._units else np.empty(0, dtype=np.int64)
        priority = np.concatenate(self._priority) if self._priority else np.empty(0, dtype=np.float64)
        keep = np.zeros(codes.shape[0], dtype=bool)
        notes: list[str] = []

        weights = {domain: weight for domain, weight in cfg.weights.items() if weight > 0}
        available = {
            domain: int(units[codes == self._domain_index[domain]].sum()) if domain in self._domain_index else 0
            for domain in weights
        }
        missing = [domain for domain, amount in available.items() if amount == 0]
        present_weight = sum(weight for domain, weight in weights.items() if available[domain] > 0)
        if missing:
            notes.append(f"no documents for weighted domains {sorted(missing)}; remaining weights renormalized")
        effective = {
            domain: (weight / present_weight if available[domain] > 0 and present_weight else 0.0)
            for domain, weight in weights.items()
        }
        feasible = [available[d] / effective[d] for d in weights if effective[d] > 0]
        total = min(feasible) if feasible else 0.0
        if cfg.target_total is not None:
            if cfg.target_total > total:
                notes.append(
                    f"target_total={cfg.target_total} exceeds what the weights allow without upsampling "
                    f"({total:.0f}); using {total:.0f}"
                )
            else:
                total = float(cfg.target_total)

        allocations: list[DomainAllocation] = []
        for domain, weight in weights.items():
            target = effective[domain] * total
            selected_docs = selected_units = 0
            if domain in self._domain_index:
                members = np.flatnonzero(codes == self._domain_index[domain])
                ordered = members[np.argsort(priority[members], kind="stable")]
                cumulative = np.cumsum(units[ordered])
                chosen = ordered[cumulative <= target + 1e-9]
                keep[chosen] = True
                selected_docs = int(chosen.shape[0])
                selected_units = int(units[chosen].sum())
                available_docs = int(members.shape[0])
            else:
                available_docs = 0
            allocations.append(
                DomainAllocation(
                    domain,
                    weight,
                    available_docs,
                    available[domain],
                    target,
                    selected_docs,
                    selected_units,
                )
            )

        listed_codes = {self._domain_index[d] for d in weights if d in self._domain_index}
        unlisted = ~np.isin(codes, list(listed_codes)) if codes.shape[0] else np.zeros(0, dtype=bool)
        if cfg.unlisted_domains == "keep":
            keep |= unlisted
        plan = MixturePlan(
            cfg.unit,
            total,
            tuple(allocations),
            cfg.unlisted_domains,
            int(unlisted.sum()),
            tuple(notes),
        )
        return keep, plan

    @staticmethod
    def save(path: Path, keep: npt.NDArray[np.bool_]) -> None:
        np.save(path, keep, allow_pickle=False)

    @staticmethod
    def load(path: Path) -> npt.NDArray[np.bool_]:
        loaded: npt.NDArray[np.bool_] = np.load(path, allow_pickle=False)
        return loaded
