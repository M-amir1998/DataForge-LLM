"""Hugging Face datasets in streaming mode (optional ``hf`` extra).

Only the adapter imports ``datasets``; nothing else in DataForge depends on it.
Remote code execution is never enabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import HuggingFaceSourceConfig
from dataforge.domain.hashing import fingerprint
from dataforge.errors import ConfigError, OptionalDependencyMissing, SourceNotFound
from dataforge.sources.base import (
    RawRecord,
    RecordError,
    SourceDescription,
    SourceReader,
    record_from_mapping,
)


def _load_dataset(cfg: HuggingFaceSourceConfig) -> Any:
    try:
        import datasets  # noqa: PLC0415 - optional extra, imported on use
    except ImportError as exc:
        raise OptionalDependencyMissing("Hugging Face sources", "hf") from exc
    try:
        return datasets.load_dataset(
            cfg.dataset,
            cfg.name,
            split=cfg.split,
            revision=cfg.revision,
            streaming=cfg.streaming,
            trust_remote_code=False,
        )
    except FileNotFoundError as exc:
        raise SourceNotFound(f"Hugging Face dataset not found: {cfg.dataset}") from exc


class HuggingFaceSourceReader(SourceReader):
    def __init__(self, source: ResolvedSource) -> None:
        super().__init__(source)
        if not isinstance(source.config, HuggingFaceSourceConfig):
            raise ConfigError(f"source '{source.source_id}' is not a Hugging Face source")
        self._cfg: HuggingFaceSourceConfig = source.config

    @property
    def uri(self) -> str:
        cfg = self._cfg
        name = f"/{cfg.name}" if cfg.name else ""
        return f"hf://{cfg.dataset}{name}@{cfg.revision or 'unpinned'}/{cfg.split}"

    def describe(self) -> SourceDescription:
        cfg = self._cfg
        notes: tuple[str, ...] = ()
        if cfg.revision is None:
            notes = ("revision is not pinned: re-runs may read different data",)
        return SourceDescription(
            source_id=self.source_id,
            type="huggingface",
            uri=self.uri,
            files=(),
            total_bytes=None,
            fingerprint=fingerprint(
                {
                    "dataset": cfg.dataset,
                    "name": cfg.name,
                    "split": cfg.split,
                    "revision": cfg.revision,
                    "max_records": cfg.max_records,
                    "text_field": cfg.text_field,
                }
            ),
            reproducible=cfg.revision is not None,
            notes=notes,
        )

    def iter_records(self) -> Iterator[RawRecord | RecordError]:
        dataset = _load_dataset(self._cfg)
        produced = 0
        for index, row in enumerate(dataset):
            if self._cfg.max_records is not None and produced >= self._cfg.max_records:
                return
            record = record_from_mapping(
                row,
                config=self._cfg,
                source_id=self.source_id,
                source_uri=self.uri,
                locator=f"{self.uri}#{index}",
                mime_type="text/plain",
                source_fingerprint=self._cfg.revision,
            )
            if isinstance(record, RawRecord):
                produced += 1
            yield record
