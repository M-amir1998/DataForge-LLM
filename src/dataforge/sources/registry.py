"""Source registry: named, typed, governed sources and the readers that serve them."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

from dataforge.config.loader import LoadedPipelineConfig, ResolvedSource, load_source_registry
from dataforge.errors import SourceNotFound
from dataforge.sources.base import SourceReader
from dataforge.sources.code import CodeSourceReader
from dataforge.sources.huggingface import HuggingFaceSourceReader
from dataforge.sources.local import LocalSourceReader
from dataforge.sources.synthetic import SyntheticSourceReader

ReaderFactory = Callable[[ResolvedSource], SourceReader]

READER_FACTORIES: Final[dict[str, ReaderFactory]] = {
    "local": LocalSourceReader,
    "code": CodeSourceReader,
    "huggingface": HuggingFaceSourceReader,
    "synthetic": SyntheticSourceReader,
}


def build_reader(source: ResolvedSource) -> SourceReader:
    return READER_FACTORIES[source.config.type](source)


class SourceRegistry:
    """An ordered collection of sources. Order is part of the pipeline's determinism."""

    def __init__(self, sources: Sequence[ResolvedSource]) -> None:
        self._sources = {source.source_id: source for source in sources}
        self._readers: dict[str, SourceReader] = {}

    @classmethod
    def from_file(cls, path: Path) -> SourceRegistry:
        registry, base_dir = load_source_registry(path)
        return cls([ResolvedSource(sid, config, base_dir) for sid, config in registry.sources.items()])

    @classmethod
    def from_pipeline(cls, loaded: LoadedPipelineConfig) -> SourceRegistry:
        return cls(loaded.sources)

    def __iter__(self) -> Iterator[ResolvedSource]:
        return iter(self._sources.values())

    def __len__(self) -> int:
        return len(self._sources)

    def ids(self) -> list[str]:
        return list(self._sources)

    def get(self, source_id: str) -> ResolvedSource:
        try:
            return self._sources[source_id]
        except KeyError:
            raise SourceNotFound(f"unknown source '{source_id}'; known: {self.ids()}") from None

    def reader(self, source_id: str) -> SourceReader:
        if source_id not in self._readers:
            self._readers[source_id] = build_reader(self.get(source_id))
        return self._readers[source_id]

    def readers(self) -> list[SourceReader]:
        return [self.reader(source_id) for source_id in self._sources]
