"""Source-code repositories on local disk: one document per source file.

Licenses are never inferred from LICENSE files; the registry entry must declare them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import CodeSourceConfig
from dataforge.domain.hashing import sha256_file
from dataforge.errors import ConfigError, DecodingError
from dataforge.parsing.encoding import decode_bytes
from dataforge.parsing.formats import mime_type, programming_language
from dataforge.sources.base import (
    FileFingerprint,
    RawRecord,
    RecordError,
    SourceDescription,
    SourceReader,
)
from dataforge.sources.files import SourceFile, discover_files

_BINARY_SNIFF_BYTES: Final = 8192


def looks_binary(data: bytes) -> bool:
    """Heuristic used by git and most code-dataset pipelines: a NUL byte means binary."""
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


class CodeSourceReader(SourceReader):
    def __init__(self, source: ResolvedSource) -> None:
        super().__init__(source)
        if not isinstance(source.config, CodeSourceConfig):
            raise ConfigError(f"source '{source.source_id}' is not a code source")
        self._cfg: CodeSourceConfig = source.config
        self._files: list[SourceFile] | None = None
        self._hashes: dict[str, str] = {}

    def files(self) -> list[SourceFile]:
        if self._files is None:
            discovered = discover_files(
                self.source.base_dir,
                self._cfg.path,
                include=self._cfg.include,
                exclude=self._cfg.exclude,
                skip_dirs=self._cfg.skip_dirs,
            )
            self._files = [f for f in discovered if programming_language(PurePosixPath(f.relative))]
        return self._files

    def estimated_bytes(self) -> int | None:
        return sum(f.size for f in self.files())

    def describe(self) -> SourceDescription:
        fingerprints = []
        for file in self.files():
            digest = self._hashes.get(file.relative) or sha256_file(file.path)
            self._hashes[file.relative] = digest
            fingerprints.append(FileFingerprint(file.relative, file.size, digest))
        files = tuple(fingerprints)
        return SourceDescription(
            source_id=self.source_id,
            type="code",
            uri=self._cfg.path,
            files=files,
            total_bytes=sum(f.size for f in files),
            fingerprint=SourceDescription.fingerprint_files(files),
            reproducible=True,
        )

    def iter_records(self) -> Iterator[RawRecord | RecordError]:
        produced = 0
        for file in self.files():
            if self._cfg.max_records is not None and produced >= self._cfg.max_records:
                return
            if file.size > self._cfg.max_file_bytes:
                yield RecordError(self.source_id, file.relative, "file_too_large", "")
                continue
            data = file.path.read_bytes()
            if looks_binary(data):
                yield RecordError(self.source_id, file.relative, "binary_file", "")
                continue
            try:
                decoded = decode_bytes(data, "utf-8", "strict")
            except UnicodeDecodeError as exc:
                if self._cfg.on_decode_error == "fail":
                    raise DecodingError(f"invalid UTF-8 in {file.relative}") from exc
                if self._cfg.on_decode_error == "skip":
                    yield RecordError(self.source_id, file.relative, "invalid_encoding", "")
                    continue
                decoded = decode_bytes(data, "utf-8", "replace")
            name = PurePosixPath(file.relative)
            produced += 1
            yield RawRecord(
                source_id=self.source_id,
                source_uri=file.relative,
                locator=file.relative,
                content=decoded.text,
                mime_type=mime_type(name),
                record_id=file.relative,
                source_fingerprint=self._hashes.get(file.relative),
                programming_language=programming_language(name),
                decode_replaced=decoded.replaced,
            )
