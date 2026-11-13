"""Local filesystem sources: text files, JSONL, JSON, CSV, Parquet, and archives of them."""

from __future__ import annotations

import csv
import gzip
import io
import json
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import PurePosixPath
from typing import IO, Final, cast

import pyarrow.parquet as pq

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import LocalSourceConfig
from dataforge.domain.hashing import sha256_file
from dataforge.errors import ConfigError, DecodingError, SourceFormatError
from dataforge.parsing.encoding import decode_bytes
from dataforge.parsing.formats import (
    RecordFormat,
    is_gzip,
    mime_type,
    programming_language,
    record_format,
)
from dataforge.sources.archives import is_archive, iter_archive_members
from dataforge.sources.base import (
    MAX_RECORD_BYTES,
    FileFingerprint,
    RawRecord,
    RecordError,
    SourceDescription,
    SourceReader,
    parse_json_object,
    record_from_mapping,
)
from dataforge.sources.files import SourceFile, discover_files

Opener = Callable[[], IO[bytes]]
_UTF8_BOM: Final = b"\xef\xbb\xbf"
_PARSER_MIME: Final = {"html": "text/html", "markdown": "text/markdown", "plain": "text/plain"}


@lru_cache(maxsize=1)
def _raise_csv_field_limit() -> None:
    """csv's field limit is process-global; raise it once to the record size cap."""
    csv.field_size_limit(MAX_RECORD_BYTES)


def iter_bounded_lines(handle: IO[bytes], limit: int) -> Iterator[tuple[int, bytes | None]]:
    """Yield ``(line_number, line)``; oversized lines yield ``None`` and are skipped safely."""
    line_number = 0
    while True:
        line = handle.readline(limit + 1)
        if not line:
            return
        line_number += 1
        if len(line) > limit and not line.endswith(b"\n"):
            while True:
                rest = handle.readline(limit)
                if not rest or rest.endswith(b"\n"):
                    break
            yield line_number, None
            continue
        yield line_number, line


class LocalSourceReader(SourceReader):
    def __init__(self, source: ResolvedSource) -> None:
        super().__init__(source)
        if not isinstance(source.config, LocalSourceConfig):
            raise ConfigError(f"source '{source.source_id}' is not a local source")
        self._cfg: LocalSourceConfig = source.config
        self._files: list[SourceFile] | None = None
        self._hashes: dict[str, str] = {}

    # ------------------------------------------------------------------ discovery

    def files(self) -> list[SourceFile]:
        if self._files is None:
            self._files = discover_files(
                self.source.base_dir,
                self._cfg.path,
                include=self._cfg.include,
                exclude=self._cfg.exclude,
            )
        return self._files

    def estimated_bytes(self) -> int | None:
        return sum(f.size for f in self.files())

    def describe(self) -> SourceDescription:
        fingerprints: list[FileFingerprint] = []
        for file in self.files():
            digest = self._hashes.get(file.relative) or sha256_file(file.path)
            self._hashes[file.relative] = digest
            fingerprints.append(FileFingerprint(file.relative, file.size, digest))
        files = tuple(fingerprints)
        return SourceDescription(
            source_id=self.source_id,
            type="local",
            uri=self._cfg.path,
            files=files,
            total_bytes=sum(f.size for f in files),
            fingerprint=SourceDescription.fingerprint_files(files),
            reproducible=True,
        )

    # ------------------------------------------------------------------ reading

    def iter_records(self) -> Iterator[RawRecord | RecordError]:
        produced = 0
        limit = self._cfg.max_records
        for file in self.files():
            for item in self._iter_file(file):
                if isinstance(item, RawRecord):
                    if limit is not None and produced >= limit:
                        return
                    produced += 1
                yield item

    def _iter_file(self, file: SourceFile) -> Iterator[RawRecord | RecordError]:
        fingerprint = self._hashes.get(file.relative)
        if is_archive(file.path):
            for member in iter_archive_members(file.path):
                locator = f"{file.relative}!{member.name}"
                if member.data is None:
                    yield RecordError(self.source_id, locator, member.skipped_reason or "skipped", "")
                    continue
                payload = member.data
                yield from self._dispatch(
                    lambda payload=payload: io.BytesIO(payload),  # type: ignore[misc]
                    PurePosixPath(member.name),
                    uri=file.relative,
                    locator=locator,
                    fingerprint=fingerprint,
                )
            return

        def opener() -> IO[bytes]:
            if is_gzip(file.path):
                return cast(IO[bytes], gzip.open(file.path, "rb"))
            return file.path.open("rb")

        yield from self._dispatch(
            opener,
            PurePosixPath(file.relative),
            uri=file.relative,
            locator=file.relative,
            fingerprint=fingerprint,
        )

    def _format(self, name: PurePosixPath) -> RecordFormat:
        return record_format(name) if self._cfg.format == "auto" else self._cfg.format

    def _mime(self, name: PurePosixPath, structured: bool) -> str:
        parser = self._cfg.parser
        if parser in _PARSER_MIME:
            return _PARSER_MIME[parser]
        if parser == "code":
            return mime_type(name) if mime_type(name).startswith("text/x-") else "text/x-code"
        return "text/plain" if structured else mime_type(name)

    def _dispatch(
        self,
        opener: Opener,
        name: PurePosixPath,
        *,
        uri: str,
        locator: str,
        fingerprint: str | None,
    ) -> Iterator[RawRecord | RecordError]:
        fmt = self._format(name)
        try:
            if fmt == "text":
                yield from self._read_text(opener, name, uri, locator, fingerprint)
            elif fmt == "jsonl":
                yield from self._read_jsonl(opener, uri, locator, fingerprint)
            elif fmt == "json":
                yield from self._read_json(opener, uri, locator, fingerprint)
            elif fmt == "csv":
                yield from self._read_csv(opener, name, uri, locator, fingerprint)
            else:
                yield from self._read_parquet(opener, uri, locator, fingerprint)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise SourceFormatError(f"cannot read {locator}: {exc}") from exc

    def _decode(self, data: bytes, locator: str) -> tuple[str, bool] | RecordError:
        policy = self._cfg.on_decode_error
        try:
            decoded = decode_bytes(data, self._cfg.encoding, "strict" if policy != "replace" else "replace")
        except (UnicodeDecodeError, LookupError) as exc:
            if policy == "fail":
                raise DecodingError(f"invalid encoding in {locator}") from exc
            return RecordError(self.source_id, locator, "invalid_encoding", "undecodable bytes")
        return decoded.text, decoded.replaced

    def _read_text(
        self, opener: Opener, name: PurePosixPath, uri: str, locator: str, fingerprint: str | None
    ) -> Iterator[RawRecord | RecordError]:
        with opener() as handle:
            data = handle.read(self._cfg.max_file_bytes + 1)
        if len(data) > self._cfg.max_file_bytes:
            yield RecordError(self.source_id, locator, "file_too_large", f"> {self._cfg.max_file_bytes} bytes")
            return
        decoded = self._decode(data, locator)
        if isinstance(decoded, RecordError):
            yield decoded
            return
        text, replaced = decoded
        yield RawRecord(
            source_id=self.source_id,
            source_uri=uri,
            locator=locator,
            content=text,
            mime_type=self._mime(name, structured=False),
            record_id=locator,
            source_fingerprint=fingerprint,
            programming_language=programming_language(name),
            decode_replaced=replaced,
        )

    def _structured(
        self, row: dict[str, object], uri: str, locator: str, fingerprint: str | None
    ) -> RawRecord | RecordError:
        return record_from_mapping(
            row,
            config=self._cfg,
            source_id=self.source_id,
            source_uri=uri,
            locator=locator,
            mime_type=self._mime(PurePosixPath(uri), structured=True),
            source_fingerprint=fingerprint,
        )

    def _read_jsonl(
        self, opener: Opener, uri: str, locator: str, fingerprint: str | None
    ) -> Iterator[RawRecord | RecordError]:
        with opener() as handle:
            for line_number, raw in iter_bounded_lines(handle, MAX_RECORD_BYTES):
                record_locator = f"{locator}#L{line_number}"
                if raw is None:
                    yield RecordError(self.source_id, record_locator, "record_too_large", "")
                    continue
                payload = raw.removeprefix(_UTF8_BOM) if line_number == 1 else raw
                if not payload.strip():
                    continue
                decoded = self._decode(payload, record_locator)
                if isinstance(decoded, RecordError):
                    yield decoded
                    continue
                line, replaced = decoded
                try:
                    row = parse_json_object(line)
                except json.JSONDecodeError as exc:
                    yield RecordError(self.source_id, record_locator, "invalid_json", f"{exc.msg} (column {exc.colno})")
                    continue
                if row is None:
                    yield RecordError(self.source_id, record_locator, "not_an_object", "")
                    continue
                record = self._structured(row, uri, record_locator, fingerprint)
                if isinstance(record, RawRecord):
                    record.decode_replaced = replaced
                yield record

    def _read_json(
        self, opener: Opener, uri: str, locator: str, fingerprint: str | None
    ) -> Iterator[RawRecord | RecordError]:
        with opener() as handle:
            data = handle.read(self._cfg.max_json_bytes + 1)
        if len(data) > self._cfg.max_json_bytes:
            raise SourceFormatError(
                f"{locator}: JSON arrays are not streamable and this file exceeds "
                f"max_json_bytes={self._cfg.max_json_bytes}; convert it to JSONL"
            )
        decoded = self._decode(data, locator)
        if isinstance(decoded, RecordError):
            yield decoded
            return
        try:
            payload = json.loads(decoded[0])
        except json.JSONDecodeError as exc:
            raise SourceFormatError(
                f"{locator}: invalid JSON ({exc.msg}, line {exc.lineno} column {exc.colno})"
            ) from exc
        if not isinstance(payload, list):
            raise SourceFormatError(f"{locator}: expected a top-level JSON array of objects")
        for index, row in enumerate(payload):
            record_locator = f"{locator}#{index}"
            if not isinstance(row, dict):
                yield RecordError(self.source_id, record_locator, "not_an_object", "")
                continue
            yield self._structured(row, uri, record_locator, fingerprint)

    def _read_csv(
        self, opener: Opener, name: PurePosixPath, uri: str, locator: str, fingerprint: str | None
    ) -> Iterator[RawRecord | RecordError]:
        _raise_csv_field_limit()
        delimiter = (
            "\t" if name.suffix.lower() == ".tsv" and self._cfg.csv_delimiter == "," else self._cfg.csv_delimiter
        )
        errors = "strict" if self._cfg.on_decode_error == "fail" else "replace"
        with opener() as handle:
            stream = io.TextIOWrapper(handle, encoding="utf-8-sig", errors=errors, newline="")
            reader = csv.DictReader(stream, delimiter=delimiter)
            index = 0
            while True:
                record_locator = f"{locator}#row={index}"
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    yield RecordError(self.source_id, record_locator, "invalid_csv_row", str(exc))
                    index += 1
                    continue
                except UnicodeDecodeError as exc:
                    raise DecodingError(f"invalid UTF-8 in {locator}") from exc
                index += 1
                yield self._structured(dict(row), uri, record_locator, fingerprint)

    def _read_parquet(
        self, opener: Opener, uri: str, locator: str, fingerprint: str | None
    ) -> Iterator[RawRecord | RecordError]:
        cfg = self._cfg
        wanted = {
            cfg.text_field,
            cfg.id_field,
            cfg.url_field,
            cfg.title_field,
            cfg.timestamp_field,
            cfg.license_field,
            *cfg.metadata_fields,
        } - {None}
        with opener() as handle:
            try:
                parquet = pq.ParquetFile(handle)
            except Exception as exc:
                raise SourceFormatError(f"{locator}: not a readable Parquet file ({exc})") from exc
            available = set(parquet.schema_arrow.names)
            if cfg.text_field not in available:
                raise SourceFormatError(f"{locator}: Parquet file has no column '{cfg.text_field}'")
            columns = sorted(str(c) for c in wanted if c in available)
            index = 0
            for batch in parquet.iter_batches(batch_size=1024, columns=columns):
                for row in batch.to_pylist():
                    yield self._structured(row, uri, f"{locator}#row={index}", fingerprint)
                    index += 1
