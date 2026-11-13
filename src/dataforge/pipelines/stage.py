"""Stage contracts.

Two kinds of stage exist:

* :class:`StreamingStage` - decides per document (normalize, PII, quality, ...). Stages are
  pure per batch: counters and artifact rows go to an :class:`Emitter` instead of hidden
  state, which keeps resume exact and leaves room for parallel execution. Order-dependent
  stages declare ``stateful = True`` and snapshot their state at every checkpoint.
* :class:`BarrierStage` - needs the whole stream before deciding (dedup, mixtures). It
  *observes* compact keys while the upstream segment is written, *finalizes* decisions
  once, and *applies* them while the next segment reads the materialized data. Decisions
  are indexed by document ordinal, which is stable because segment outputs are read back
  in write order.

Stages return a :class:`Rejection` instead of dropping documents themselves; the executor
turns it into a removal (``mode: filter``) or a flag (``mode: annotate``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from dataforge.config.loader import LoadedPipelineConfig
from dataforge.config.models import StageConfig
from dataforge.config.sources import SourceBase
from dataforge.domain.document import Document
from dataforge.tokenization.estimators import TokenEstimator

StageKind = Literal["streaming", "barrier"]


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str


StageResult = Document | Rejection


@dataclass(slots=True)
class Emitter:
    """Per-batch sink for a stage's counters and content-free artifact rows."""

    counters: Counter[str] = field(default_factory=Counter)
    artifacts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def count(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    def artifact(self, name: str, row: dict[str, Any]) -> None:
        self.artifacts.setdefault(name, []).append(row)


@dataclass(slots=True)
class StageStats:
    """Mergeable accounting for one stage (summed over batches, parts, and resumes)."""

    name: str
    kind: str = "streaming"
    mode: str = "filter"
    documents_in: int = 0
    documents_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    seconds: float = 0.0
    rejected: Counter[str] = field(default_factory=Counter)
    rejected_bytes: Counter[str] = field(default_factory=Counter)
    flagged: Counter[str] = field(default_factory=Counter)
    counters: Counter[str] = field(default_factory=Counter)

    def merge(self, other: StageStats) -> None:
        self.documents_in += other.documents_in
        self.documents_out += other.documents_out
        self.bytes_in += other.bytes_in
        self.bytes_out += other.bytes_out
        self.seconds += other.seconds
        self.rejected.update(other.rejected)
        self.rejected_bytes.update(other.rejected_bytes)
        self.flagged.update(other.flagged)
        self.counters.update(other.counters)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "mode": self.mode,
            "documents_in": self.documents_in,
            "documents_out": self.documents_out,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "seconds": round(self.seconds, 6),
            "rejected": dict(sorted(self.rejected.items())),
            "rejected_bytes": dict(sorted(self.rejected_bytes.items())),
            "flagged": dict(sorted(self.flagged.items())),
            "counters": dict(sorted(self.counters.items())),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StageStats:
        return cls(
            name=str(data["name"]),
            kind=str(data.get("kind", "streaming")),
            mode=str(data.get("mode", "filter")),
            documents_in=int(data.get("documents_in", 0)),
            documents_out=int(data.get("documents_out", 0)),
            bytes_in=int(data.get("bytes_in", 0)),
            bytes_out=int(data.get("bytes_out", 0)),
            seconds=float(data.get("seconds", 0.0)),
            rejected=Counter(data.get("rejected", {})),
            rejected_bytes=Counter(data.get("rejected_bytes", {})),
            flagged=Counter(data.get("flagged", {})),
            counters=Counter(data.get("counters", {})),
        )


@dataclass(slots=True)
class StageContext:
    """Shared, read-only services available to every stage."""

    loaded: LoadedPipelineConfig
    token_estimator: TokenEstimator

    @property
    def base_dir(self) -> Path:
        return self.loaded.base_dir

    def source_config(self, source_id: str) -> SourceBase | None:
        for source in self.loaded.sources:
            if source.source_id == source_id:
                return source.config
        return None


class Stage(ABC):
    name: ClassVar[str]
    kind: ClassVar[StageKind] = "streaming"
    #: Bump when stage semantics change in a way configs cannot express.
    version: ClassVar[int] = 1
    description: ClassVar[str] = ""

    def __init__(self, config: StageConfig, context: StageContext) -> None:
        self.config = config
        self.context = context

    @property
    def mode(self) -> str:
        return self.config.mode

    def resources(self) -> dict[str, str]:
        """Fingerprints of external inputs (benchmark files, tokenizers) that affect output."""
        return {}

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "version": self.version,
            "config": self.config.model_dump(mode="json"),
            "resources": self.resources(),
        }


class StreamingStage(Stage):
    stateful: ClassVar[bool] = False

    @abstractmethod
    def process(self, document: Document, emit: Emitter) -> StageResult:
        """Transform or reject one document."""

    def process_batch(self, documents: Sequence[Document], emit: Emitter) -> list[StageResult]:
        return [self.process(document, emit) for document in documents]

    def save_state(self, path: Path) -> None:
        """Persist order-dependent state (stateful stages only)."""

    def load_state(self, path: Path) -> None:
        """Restore state saved by :meth:`save_state`."""


class BarrierStage(Stage):
    kind: ClassVar[StageKind] = "barrier"

    @abstractmethod
    def reset(self, spill_dir: Path) -> None:
        """Start a fresh observation (also used before replaying on resume)."""

    @abstractmethod
    def observe(self, documents: Sequence[Document]) -> None:
        """Record compact keys for documents in output order."""

    @abstractmethod
    def finalize(self, decisions_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        """Compute and persist decisions; return a summary and report tables."""

    @abstractmethod
    def load(self, decisions_dir: Path) -> dict[str, Any]:
        """Load persisted decisions (cache hit or resume); return the stored summary."""

    @abstractmethod
    def apply(self, document: Document, ordinal: int, emit: Emitter) -> StageResult:
        """Apply the decision for the document at ``ordinal`` of the observed stream."""

    def apply_batch(self, documents: Sequence[Document], first_ordinal: int, emit: Emitter) -> list[StageResult]:
        return [self.apply(doc, first_ordinal + offset, emit) for offset, doc in enumerate(documents)]
