"""Intermediate materialization: numbered Parquet *parts* written atomically.

A part is closed by writing ``part-NNNNN.parquet.tmp`` and renaming it, so a crash never
leaves a truncated part that looks complete. Readers can skip a prefix of documents
cheaply using Parquet row-count metadata, which is what makes resume inexpensive.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.domain.document import Document
from dataforge.storage.schema import DOCUMENT_SCHEMA, batch_to_documents, documents_to_table


@dataclass(frozen=True, slots=True)
class PartInfo:
    name: str
    documents: int
    file_bytes: int


def part_name(index: int) -> str:
    return f"part-{index:05d}.parquet"


class PartWriter:
    """Appends document batches to the current part; ``close_part`` commits it."""

    def __init__(self, directory: Path, compression: str = "zstd", start_index: int = 0) -> None:
        self.directory = directory
        self.compression = None if compression == "none" else compression
        self.next_index = start_index
        self._writer: pq.ParquetWriter | None = None
        self._tmp: Path | None = None
        self._documents = 0
        directory.mkdir(parents=True, exist_ok=True)

    @property
    def open_documents(self) -> int:
        return self._documents

    def write(self, documents: Sequence[Document]) -> None:
        if not documents:
            return
        if self._writer is None:
            self._tmp = self.directory / (part_name(self.next_index) + ".tmp")
            self._writer = pq.ParquetWriter(self._tmp, DOCUMENT_SCHEMA, compression=self.compression)
        self._writer.write_table(documents_to_table(documents))
        self._documents += len(documents)

    def close_part(self) -> PartInfo | None:
        """Commit the open part (if any) and return its description."""
        if self._writer is None or self._tmp is None:
            return None
        self._writer.close()
        final = self.directory / part_name(self.next_index)
        self._tmp.replace(final)
        info = PartInfo(final.name, self._documents, final.stat().st_size)
        self._writer = None
        self._tmp = None
        self._documents = 0
        self.next_index += 1
        return info

    def abort(self) -> None:
        """Drop an uncommitted part (used when a stage fails mid-part)."""
        if self._writer is not None:
            self._writer.close()
        if self._tmp is not None:
            self._tmp.unlink(missing_ok=True)
        self._writer = None
        self._tmp = None
        self._documents = 0


def list_parts(directory: Path) -> list[Path]:
    return sorted(directory.glob("part-*.parquet"))


def iter_documents(
    paths: Sequence[Path], batch_size: int, *, skip: int = 0, columns: Sequence[str] | None = None
) -> Iterator[list[Document]]:
    """Stream documents from parts in order, skipping the first ``skip`` documents."""
    remaining_skip = skip
    for path in paths:
        parquet = pq.ParquetFile(path)
        try:
            rows = parquet.metadata.num_rows
            if remaining_skip >= rows:
                remaining_skip -= rows
                continue
            for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
                if remaining_skip:
                    if remaining_skip >= batch.num_rows:
                        remaining_skip -= batch.num_rows
                        continue
                    trimmed = batch.slice(remaining_skip)
                    remaining_skip = 0
                    yield batch_to_documents(trimmed)
                    continue
                yield batch_to_documents(batch)
        finally:
            parquet.close()


def count_documents(paths: Sequence[Path]) -> int:
    total = 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        try:
            total += parquet.metadata.num_rows
        finally:
            parquet.close()
    return total


def read_table(paths: Sequence[Path], columns: Sequence[str]) -> pa.Table:
    """Read selected columns from parts (small analytical projections only)."""
    tables = [pq.read_table(path, columns=list(columns)) for path in paths]
    if not tables:
        return pa.table({name: pa.array([], type=DOCUMENT_SCHEMA.field(name).type) for name in columns})
    return pa.concat_tables(tables)
