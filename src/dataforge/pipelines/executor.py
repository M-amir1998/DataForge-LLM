"""Segment execution with checkpointing, resume, caching, and bounded memory.

Memory is bounded by one batch of documents per stage plus each barrier's compact
per-document keys; documents are never accumulated beyond the current part writer's
row-group buffer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataforge.config.models import ExecutionConfig
from dataforge.domain.document import Document
from dataforge.errors import DataForgeError, PipelineStageFailure
from dataforge.ingestion.ingest import Ingestor, InputBatch
from dataforge.pipelines.checkpoint import (
    SUCCESS_FILE,
    Checkpoint,
    SegmentProgress,
    atomic_write_text,
    prune_unrecorded,
    reset_directory,
    write_jsonl,
)
from dataforge.pipelines.graph import SegmentSpec
from dataforge.pipelines.stage import (
    BarrierStage,
    Emitter,
    Rejection,
    Stage,
    StageResult,
    StageStats,
    StreamingStage,
)
from dataforge.storage.budget import DiskGuard
from dataforge.storage.parts import PartWriter, iter_documents, list_parts
from dataforge.telemetry.metrics import MemorySampler

INGEST = "ingest"
REJECTIONS = "rejections"


@dataclass(slots=True)
class SegmentOutcome:
    spec: SegmentSpec
    fingerprint: str
    directory: Path
    stats: dict[str, StageStats]
    barrier_summary: dict[str, Any] | None
    cached: bool
    resumed_checkpoints: int
    documents_out: int
    seconds: float

    @property
    def parts(self) -> list[Path]:
        return list_parts(self.directory / "parts")


@dataclass(slots=True)
class _PartBuffer:
    stats: dict[str, StageStats] = field(default_factory=dict)
    artifacts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def stage(self, stage: Stage | str) -> StageStats:
        if isinstance(stage, str):
            name, kind, mode = stage, "source", "filter"
        else:
            name, kind, mode = stage.name, stage.kind, stage.mode
        if name not in self.stats:
            self.stats[name] = StageStats(name=name, kind=kind, mode=mode)
        return self.stats[name]

    def absorb(self, emit: Emitter, stats: StageStats) -> None:
        stats.counters.update(emit.counters)
        for name, rows in emit.artifacts.items():
            self.artifacts.setdefault(name, []).extend(rows)


def _sizes(documents: Sequence[Document]) -> int:
    return sum(doc.num_bytes for doc in documents)


class SegmentExecutor:
    def __init__(
        self,
        *,
        stages: Mapping[str, Stage],
        execution: ExecutionConfig,
        compression: str,
        guard: DiskGuard,
        run_id: str,
        logger: logging.Logger,
        sampler: MemorySampler,
    ) -> None:
        self.stages = stages
        self.execution = execution
        self.compression = compression
        self.guard = guard
        self.run_id = run_id
        self.logger = logger
        self.sampler = sampler

    # ------------------------------------------------------------------ helpers

    def _streaming(self, spec: SegmentSpec) -> list[StreamingStage]:
        stages: list[StreamingStage] = []
        for name in spec.streaming:
            stage = self.stages[name]
            if not isinstance(stage, StreamingStage):
                raise PipelineStageFailure(name, "expected a streaming stage")
            stages.append(stage)
        return stages

    def _barrier(self, name: str | None) -> BarrierStage | None:
        if name is None:
            return None
        stage = self.stages[name]
        if not isinstance(stage, BarrierStage):
            raise PipelineStageFailure(name, "expected a barrier stage")
        return stage

    def _settle(
        self,
        stage: Stage,
        documents: Sequence[Document],
        results: Sequence[StageResult],
        buffer: _PartBuffer,
    ) -> list[Document]:
        stats = buffer.stage(stage)
        kept: list[Document] = []
        for document, result in zip(documents, results, strict=True):
            if isinstance(result, Rejection):
                if stage.mode == "annotate":
                    document.metadata.flags.append(f"{stage.name}:{result.reason}")
                    stats.flagged[result.reason] += 1
                    kept.append(document)
                    continue
                size = document.num_bytes
                stats.rejected[result.reason] += 1
                stats.rejected_bytes[result.reason] += size
                buffer.artifacts.setdefault(REJECTIONS, []).append(
                    {
                        "document_id": document.document_id,
                        "source_id": document.source_id,
                        "record_id": document.provenance.record_id,
                        "stage": stage.name,
                        "reason": result.reason,
                        "bytes": size,
                    }
                )
            else:
                kept.append(result)
        return kept

    def _run_streaming(self, stage: StreamingStage, documents: list[Document], buffer: _PartBuffer) -> list[Document]:
        if not documents:
            return documents
        stats = buffer.stage(stage)
        emit = Emitter()
        bytes_in = _sizes(documents)
        started = time.perf_counter()
        try:
            results = stage.process_batch(documents, emit)
        except DataForgeError:
            raise
        except Exception as exc:
            raise PipelineStageFailure(stage.name, f"{type(exc).__name__}: {exc}") from exc
        stats.seconds += time.perf_counter() - started
        kept = self._settle(stage, documents, results, buffer)
        stats.documents_in += len(documents)
        stats.bytes_in += bytes_in
        stats.documents_out += len(kept)
        stats.bytes_out += _sizes(kept)
        buffer.absorb(emit, stats)
        return kept

    def _run_apply(
        self,
        stage: BarrierStage,
        documents: list[Document],
        first_ordinal: int,
        buffer: _PartBuffer,
    ) -> list[Document]:
        if not documents:
            return documents
        stats = buffer.stage(stage)
        emit = Emitter()
        bytes_in = _sizes(documents)
        started = time.perf_counter()
        results = stage.apply_batch(documents, first_ordinal, emit)
        stats.seconds += time.perf_counter() - started
        kept = self._settle(stage, documents, results, buffer)
        stats.documents_in += len(documents)
        stats.bytes_in += bytes_in
        stats.documents_out += len(kept)
        stats.bytes_out += _sizes(kept)
        buffer.absorb(emit, stats)
        return kept

    def _observe(self, stage: BarrierStage, documents: list[Document], buffer: _PartBuffer) -> None:
        stats = buffer.stage(stage)
        started = time.perf_counter()
        try:
            stage.observe(documents)
        except DataForgeError:
            raise
        except Exception as exc:
            raise PipelineStageFailure(stage.name, f"{type(exc).__name__}: {exc}") from exc
        stats.seconds += time.perf_counter() - started

    # ------------------------------------------------------------------ main loop

    def _input(self, source: Ingestor | Sequence[Path], skip: int) -> Iterator[InputBatch]:
        batch_size = self.execution.batch_size
        if isinstance(source, Ingestor):
            yield from source.iter_batches(batch_size, skip=skip)
            return
        cursor = skip
        for documents in iter_documents(source, batch_size, skip=skip):
            cursor += len(documents)
            yield InputBatch(documents, cursor)

    def run(
        self,
        spec: SegmentSpec,
        fingerprint: str,
        directory: Path,
        source: Ingestor | Sequence[Path],
        *,
        resume: bool = True,
        use_cache: bool = True,
    ) -> SegmentOutcome:
        started = time.perf_counter()
        streaming = self._streaming(spec)
        apply = self._barrier(spec.apply)
        observe = self._barrier(spec.observe)
        progress = SegmentProgress.load(directory, fingerprint) if (resume or use_cache) else None

        if progress is not None and progress.complete and use_cache and (directory / SUCCESS_FILE).is_file():
            cached_summary = observe.load(directory / "decisions") if observe is not None else None
            self.logger.info("segment cache hit", extra={"run_id": self.run_id, "segment": spec.index})
            return SegmentOutcome(
                spec,
                fingerprint,
                directory,
                self._merged(progress),
                cached_summary,
                True,
                len(progress.checkpoints),
                progress.documents,
                time.perf_counter() - started,
            )

        if progress is None or not resume or progress.complete:
            reset_directory(directory)
            progress = SegmentProgress(fingerprint)
            resumed = 0
        else:
            prune_unrecorded(directory, progress)
            resumed = len(progress.checkpoints)
            if resumed:
                self.logger.info(
                    "resuming segment",
                    extra={
                        "run_id": self.run_id,
                        "segment": spec.index,
                        "checkpoints": resumed,
                        "cursor": progress.cursor,
                    },
                )
        for stage in streaming:
            snapshot = directory / "state" / f"{stage.name}-{len(progress.checkpoints) - 1}.bin"
            if stage.stateful and snapshot.is_file():
                stage.load_state(snapshot)
        if observe is not None:
            observe.reset(directory / "spill")
            parts = [directory / "parts" / name for name in progress.part_names()]
            for documents in iter_documents(parts, self.execution.batch_size):
                observe.observe(documents)

        writer = PartWriter(directory / "parts", self.compression, start_index=len(progress.checkpoints))
        buffer = _PartBuffer()
        cursor = progress.cursor
        output_documents = progress.documents
        try:
            for batch in self._input(source, progress.cursor):
                documents = batch.documents
                if isinstance(source, Ingestor):
                    ingest = buffer.stage(INGEST)
                    ingest.documents_out += len(documents)
                    ingest.bytes_out += _sizes(documents)
                    ingest.counters.update(batch.counters)
                    for name, rows in batch.artifacts.items():
                        buffer.artifacts.setdefault(name, []).extend(rows)
                first_ordinal = batch.cursor - len(documents)
                if apply is not None:
                    documents = self._run_apply(apply, documents, first_ordinal, buffer)
                for stage in streaming:
                    documents = self._run_streaming(stage, documents, buffer)
                if observe is not None and documents:
                    self._observe(observe, documents, buffer)
                writer.write(documents)
                output_documents += len(documents)
                cursor = batch.cursor
                self.sampler.sample()
                if writer.open_documents >= self.execution.part_max_documents:
                    self._commit(directory, progress, writer, buffer, streaming=streaming, cursor=cursor)
                    buffer = _PartBuffer()
            self._commit(directory, progress, writer, buffer, streaming=streaming, cursor=cursor, force=True)
        except BaseException:
            writer.abort()
            raise

        summary: dict[str, Any] | None = None
        if observe is not None:
            finalize_started = time.perf_counter()
            summary, tables = observe.finalize(directory / "decisions")
            for name, rows in tables.items():
                write_jsonl(directory / "decisions" / f"{name}.jsonl", rows)
            progress.barrier_summary = summary
            finalize_seconds = time.perf_counter() - finalize_started
            self.logger.info(
                "barrier finalized",
                extra={
                    "run_id": self.run_id,
                    "stage": observe.name,
                    "elapsed_s": round(finalize_seconds, 3),
                },
            )
        progress.complete = True
        progress.save(directory)
        atomic_write_text(directory / SUCCESS_FILE, fingerprint + "\n")
        return SegmentOutcome(
            spec,
            fingerprint,
            directory,
            self._merged(progress),
            summary,
            False,
            resumed,
            output_documents,
            time.perf_counter() - started,
        )

    def _commit(
        self,
        directory: Path,
        progress: SegmentProgress,
        writer: PartWriter,
        buffer: _PartBuffer,
        *,
        streaming: Sequence[StreamingStage],
        cursor: int,
        force: bool = False,
    ) -> None:
        if not force and writer.open_documents == 0:
            return
        self.guard.check()
        index = len(progress.checkpoints)
        info = writer.close_part()
        for name, rows in buffer.artifacts.items():
            write_jsonl(directory / "artifacts" / name / f"part-{index:05d}.jsonl", rows)
        for stage in streaming:
            if stage.stateful:
                state_dir = directory / "state"
                state_dir.mkdir(parents=True, exist_ok=True)
                stage.save_state(state_dir / f"{stage.name}-{index}.bin")
        progress.checkpoints.append(
            Checkpoint(
                index,
                info.name if info else None,
                info.documents if info else 0,
                cursor,
                {name: stats.as_dict() for name, stats in buffer.stats.items()},
            )
        )
        progress.save(directory)
        writer.next_index = len(progress.checkpoints)
        state_dir = directory / "state"
        if state_dir.is_dir():
            for path in state_dir.iterdir():
                if not path.stem.endswith(f"-{index}"):
                    path.unlink()
        self.logger.info(
            "checkpoint",
            extra={
                "run_id": self.run_id,
                "segment": directory.name,
                "checkpoint": index,
                "documents": info.documents if info else 0,
                "cursor": cursor,
                "rss_mb": round(self.sampler.peak_bytes / 2**20, 1),
            },
        )

    @staticmethod
    def _merged(progress: SegmentProgress) -> dict[str, StageStats]:
        merged: dict[str, StageStats] = {}
        for checkpoint in progress.checkpoints:
            for name, data in checkpoint.stats.items():
                stats = StageStats.from_dict(data)
                if name in merged:
                    merged[name].merge(stats)
                else:
                    merged[name] = stats
        return merged
