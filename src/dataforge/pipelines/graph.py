"""Pipeline graph: stage order, dependency validation, and segmentation.

Stages form a validated chain. Consecutive streaming stages are *fused* into one segment
that reads its input once; each barrier ends a segment (its observation happens while that
segment is written) and its decisions are applied at the start of the next segment. So the
number of full-corpus materializations equals the number of barriers plus one final
pre-shard materialization.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from dataforge.config.models import DEFAULT_STAGE_ORDER, PipelineConfig, StageName, StagesConfig
from dataforge.errors import ConfigError

_DESCRIPTIONS: Final[dict[str, str]] = {
    "normalize": "Unicode / whitespace normalization, code detection",
    "language": "language identification and filtering",
    "exact_dedup": "exact duplicate removal (content hash)",
    "paragraph_dedup": "repeated-paragraph removal (Bloom filter)",
    "near_dedup": "near-duplicate clustering (MinHash + LSH)",
    "pii": "PII detection / redaction",
    "secrets": "credential detection / redaction",
    "license": "license and usage-policy gate",
    "quality": "heuristic quality scoring",
    "contamination": "benchmark contamination detection",
    "classify": "domain classification",
    "mixture": "weighted domain mixture",
    "split": "group-aware train/validation/test split",
    "analyze": "token and length analysis",
}

Requirement = Callable[[StagesConfig], tuple[StageName, ...]]


def _quality_if_policy(stage: StageName) -> Requirement:
    def requirement(stages: StagesConfig) -> tuple[StageName, ...]:
        policy = getattr(stages, stage).policy
        return ("quality",) if policy == "keep_highest_quality" else ()

    return requirement


_REQUIREMENTS: Final[dict[str, Requirement]] = {
    "exact_dedup": _quality_if_policy("exact_dedup"),
    "near_dedup": _quality_if_policy("near_dedup"),
    "mixture": lambda _stages: ("classify",),
    "split": lambda stages: ("classify",) if stages.split.strategy == "domain" else (),
}


def _no_requirements(_stages: StagesConfig) -> tuple[StageName, ...]:
    return ()


def stage_kind(name: StageName, stages: StagesConfig) -> str:
    if name in {"exact_dedup", "near_dedup", "mixture"}:
        return "barrier"
    if name == "split" and stages.split.strategy == "time":
        return "barrier"
    return "streaming"


@dataclass(frozen=True, slots=True)
class PlanNode:
    name: StageName
    kind: str
    mode: str
    requires: tuple[StageName, ...]
    description: str


@dataclass(frozen=True, slots=True)
class SegmentSpec:
    index: int
    apply: StageName | None
    streaming: tuple[StageName, ...]
    observe: StageName | None

    @property
    def stage_names(self) -> tuple[StageName, ...]:
        names: list[StageName] = []
        if self.apply:
            names.append(self.apply)
        names.extend(self.streaming)
        if self.observe:
            names.append(self.observe)
        return tuple(names)

    def label(self) -> str:
        parts = ["ingest"] if self.index == 0 else []
        if self.apply:
            parts.append(f"apply:{self.apply}")
        parts.extend(self.streaming)
        if self.observe:
            parts.append(f"observe:{self.observe}")
        return " -> ".join(parts) or "(passthrough)"


@dataclass(frozen=True, slots=True)
class PipelinePlan:
    nodes: tuple[PlanNode, ...]
    segments: tuple[SegmentSpec, ...]
    until: StageName | None = None
    disabled: tuple[StageName, ...] = field(default_factory=tuple)
    ingest_only: bool = False

    @property
    def materializations(self) -> int:
        return len(self.segments)

    @property
    def produces_dataset(self) -> bool:
        return self.until is None and not self.ingest_only


def build_plan(config: PipelineConfig, until: StageName | None = None, *, ingest_only: bool = False) -> PipelinePlan:
    """Validate the stage order and split it into segments.

    Raises:
        ConfigError: unknown/missing stages in a custom order, unmet dependencies, or an
            ``until`` stage that is not enabled.
    """
    stages = config.stages
    order = config.stage_order()
    enabled = [name for name in order if stages.get(name).enabled]
    if config.pipeline.stage_order is not None:
        omitted = [n for n in DEFAULT_STAGE_ORDER if stages.get(n).enabled and n not in order]
        if omitted:
            raise ConfigError(f"stage_order omits enabled stages: {omitted}")
    position = {name: index for index, name in enumerate(enabled)}
    nodes: list[PlanNode] = []
    for name in enabled:
        requires = _REQUIREMENTS.get(name, _no_requirements)(stages)
        for dependency in requires:
            if dependency not in position:
                raise ConfigError(f"stage '{name}' requires stage '{dependency}' to be enabled")
            if position[dependency] > position[name]:
                raise ConfigError(f"stage '{name}' requires '{dependency}' to run before it; fix stage_order")
        nodes.append(PlanNode(name, stage_kind(name, stages), stages.get(name).mode, requires, _DESCRIPTIONS[name]))

    disabled = tuple(name for name in DEFAULT_STAGE_ORDER if not stages.get(name).enabled)
    if ingest_only:
        return PipelinePlan((), (SegmentSpec(0, None, (), None),), None, disabled, ingest_only=True)
    if until is not None:
        if until not in position:
            raise ConfigError(f"--until stage '{until}' is not an enabled stage")
        nodes = nodes[: position[until] + 1]

    segments: list[SegmentSpec] = []
    apply: StageName | None = None
    streaming: list[StageName] = []
    for node in nodes:
        if node.kind == "barrier":
            segments.append(SegmentSpec(len(segments), apply, tuple(streaming), node.name))
            apply, streaming = node.name, []
        else:
            streaming.append(node.name)
    ends_with_barrier = bool(nodes) and nodes[-1].kind == "barrier"
    if until is None or not ends_with_barrier:
        segments.append(SegmentSpec(len(segments), apply, tuple(streaming), None))
    return PipelinePlan(tuple(nodes), tuple(segments), until, disabled)
