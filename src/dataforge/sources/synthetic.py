"""Synthetic source: a generated, never-materialized document stream."""

from __future__ import annotations

from collections.abc import Iterator

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import SyntheticSourceConfig
from dataforge.domain.hashing import fingerprint
from dataforge.errors import ConfigError
from dataforge.sources.base import RawRecord, RecordError, SourceDescription, SourceReader
from dataforge.synthetic.stream import iter_synthetic_documents


class SyntheticSourceReader(SourceReader):
    def __init__(self, source: ResolvedSource) -> None:
        super().__init__(source)
        if not isinstance(source.config, SyntheticSourceConfig):
            raise ConfigError(f"source '{source.source_id}' is not a synthetic source")
        self._cfg: SyntheticSourceConfig = source.config

    @property
    def uri(self) -> str:
        return f"synthetic://seed={self._cfg.seed}"

    def estimated_bytes(self) -> int | None:
        if self._cfg.total_bytes is not None:
            return self._cfg.total_bytes
        return (self._cfg.num_documents or 0) * self._cfg.mean_words * 6

    def describe(self) -> SourceDescription:
        cfg = self._cfg
        return SourceDescription(
            source_id=self.source_id,
            type="synthetic",
            uri=self.uri,
            files=(),
            total_bytes=self.estimated_bytes(),
            fingerprint=fingerprint(
                {
                    "seed": cfg.seed,
                    "num_documents": cfg.num_documents,
                    "total_bytes": cfg.total_bytes,
                    "mean_words": cfg.mean_words,
                    "max_records": cfg.max_records,
                }
            ),
            reproducible=True,
        )

    def iter_records(self) -> Iterator[RawRecord | RecordError]:
        cfg = self._cfg
        limit = cfg.num_documents
        if cfg.max_records is not None:
            limit = min(limit, cfg.max_records) if limit is not None else cfg.max_records
        for document in iter_synthetic_documents(
            seed=cfg.seed,
            mean_words=cfg.mean_words,
            num_documents=limit,
            total_bytes=cfg.total_bytes,
        ):
            yield RawRecord(
                source_id=self.source_id,
                source_uri=self.uri,
                locator=f"{self.uri}#{document.record_id}",
                content=document.text,
                mime_type="text/plain",
                record_id=document.record_id,
                extra={"synthetic_domain": document.domain},
            )
