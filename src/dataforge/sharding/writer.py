"""Model-ready shard writing.

Parquet shards are rotated on *compressed* size: the writer checks the sink position after
every row group, so ``target_shard_bytes`` bounds real file sizes (to within one row group).
JSONL shards are zstd-compressed and rotated on uncompressed bytes. Final names follow the
Hugging Face convention ``{split}-{index:05d}-of-{count:05d}.{ext}`` so the dataset loads with
``datasets.load_dataset("parquet", data_dir=...)`` unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from dataforge.config.models import ShardConfig
from dataforge.domain.document import Document
from dataforge.domain.hashing import sha256_file
from dataforge.storage.schema import DOCUMENT_SCHEMA, documents_to_table


@dataclass(frozen=True, slots=True)
class ShardInfo:
    path: str
    split: str
    format: str
    compression: str
    documents: int
    bytes: int
    text_bytes: int
    tokens: int
    sha256: str


@dataclass(slots=True)
class _OpenShard:
    tmp: Path
    sink: Any
    writer: Any
    documents: int = 0
    text_bytes: int = 0
    tokens: int = 0


class SplitShardWriter:
    """Writes one split's documents into size-bounded shards under ``root/data``."""

    def __init__(self, root: Path, split: str, config: ShardConfig) -> None:
        self.root = root
        self.split = split
        self.config = config
        self.extension = "parquet" if config.format == "parquet" else "jsonl.zst"
        self._buffer: list[Document] = []
        self._open: _OpenShard | None = None
        self._closed: list[tuple[Path, _OpenShard, int]] = []
        (root / "data").mkdir(parents=True, exist_ok=True)

    def _start(self) -> _OpenShard:
        index = len(self._closed)
        tmp = self.root / "data" / f".{self.split}-{index:05d}.{self.extension}.tmp"
        if self.config.format == "parquet":
            sink = pa.OSFile(str(tmp), "wb")
            compression = None if self.config.compression == "none" else self.config.compression
            level = self.config.compression_level if compression in {"zstd", "gzip"} else None
            writer = pq.ParquetWriter(sink, DOCUMENT_SCHEMA, compression=compression, compression_level=level)
            return _OpenShard(tmp, sink, writer)
        sink = pa.CompressedOutputStream(str(tmp), "zstd")
        return _OpenShard(tmp, sink, None)

    def _size(self, shard: _OpenShard) -> int:
        return int(shard.sink.tell())

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        if self._open is None:
            self._open = self._start()
        shard = self._open
        batch = self._buffer
        self._buffer = []
        if self.config.format == "parquet":
            shard.writer.write_table(documents_to_table(batch))
        else:
            for row in documents_to_table(batch).to_pylist():
                line = json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n"
                shard.sink.write(line.encode("utf-8"))
        shard.documents += len(batch)
        shard.text_bytes += sum(doc.num_bytes for doc in batch)
        shard.tokens += sum(doc.metadata.token_estimate or 0 for doc in batch)
        if (
            shard.documents >= self.config.max_documents_per_shard
            or self._size(shard) >= self.config.target_shard_bytes
        ):
            self._finish()

    def _finish(self) -> None:
        shard = self._open
        if shard is None:
            return
        if shard.writer is not None:
            shard.writer.close()
        shard.sink.close()
        self._closed.append((shard.tmp, shard, shard.tmp.stat().st_size))
        self._open = None

    def write(self, documents: Sequence[Document]) -> None:
        for document in documents:
            self._buffer.append(document)
            room = self.config.max_documents_per_shard - (self._open.documents if self._open else 0)
            if len(self._buffer) >= min(self.config.row_group_documents, room):
                self._flush_buffer()

    def close(self) -> list[ShardInfo]:
        """Flush, finalize names (``-of-NNNNN`` needs the final count), and hash shards."""
        self._flush_buffer()
        self._finish()
        count = len(self._closed)
        infos: list[ShardInfo] = []
        for index, (tmp, shard, size) in enumerate(self._closed):
            name = f"{self.split}-{index:05d}-of-{count:05d}.{self.extension}"
            final = self.root / "data" / name
            tmp.replace(final)
            infos.append(
                ShardInfo(
                    path=f"data/{name}",
                    split=self.split,
                    format=self.config.format,
                    compression="zstd" if self.config.format == "jsonl" else self.config.compression,
                    documents=shard.documents,
                    bytes=size,
                    text_bytes=shard.text_bytes,
                    tokens=shard.tokens,
                    sha256=sha256_file(final),
                )
            )
        return infos


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("quality_signals", "pii_counts", "secret_counts"):
        value = out.get(key)
        out[key] = dict(value) if value else {}
    return out


class ShardWriter:
    """Routes documents to per-split writers (one open shard per split at a time)."""

    def __init__(self, root: Path, config: ShardConfig) -> None:
        self.root = root
        self.config = config
        self._writers: dict[str, SplitShardWriter] = {}

    def write(self, documents: Sequence[Document], default_split: str = "train") -> None:
        routed: dict[str, list[Document]] = {}
        for document in documents:
            routed.setdefault(document.metadata.split or default_split, []).append(document)
        for split, batch in routed.items():
            if split not in self._writers:
                self._writers[split] = SplitShardWriter(self.root, split, self.config)
            self._writers[split].write(batch)

    def close(self) -> list[ShardInfo]:
        shards: list[ShardInfo] = []
        for split in sorted(self._writers):
            shards.extend(self._writers[split].close())
        return shards
