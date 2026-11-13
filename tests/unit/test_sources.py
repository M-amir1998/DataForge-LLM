from __future__ import annotations

import gzip
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import CodeSourceConfig, LocalSourceConfig
from dataforge.errors import DecodingError, SourceFormatError, SourceNotFound, UnsafePathError
from dataforge.sources.archives import ArchiveLimits, iter_archive_members, validate_member_name
from dataforge.sources.base import RawRecord, RecordError, json_safe, normalize_timestamp
from dataforge.sources.code import CodeSourceReader
from dataforge.sources.files import discover_files, glob_match
from dataforge.sources.local import LocalSourceReader


def local(tmp_path: Path, **config: object) -> LocalSourceReader:
    cfg = LocalSourceConfig.model_validate({"type": "local", **config})
    return LocalSourceReader(ResolvedSource("src", cfg, tmp_path))


def records(reader: LocalSourceReader | CodeSourceReader) -> list[RawRecord | RecordError]:
    return list(reader.iter_records())


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --------------------------------------------------------------------------- files


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("a.py", "*.py", True),
        ("x/y/a.py", "*.py", True),
        ("x/y/a.py", "**/*.py", True),
        ("x/y/a.py", "x/*.py", False),
        ("x/a.py", "x/*.py", True),
        ("x/a.txt", "*.py", False),
        ("x/a1.py", "x/a?.py", True),
        ("docs/b.md", "docs/**", True),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


def test_discover_files_is_sorted_and_filtered(tmp_path: Path) -> None:
    for name in ("b/2.txt", "a/1.txt", "a/skip.log", "c/node_modules/x.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    files = discover_files(tmp_path, ".", include=("*.txt",), skip_dirs=("node_modules",))
    assert [f.relative for f in files] == ["a/1.txt", "b/2.txt"]
    with pytest.raises(SourceNotFound):
        discover_files(tmp_path, "missing")
    with pytest.raises(SourceNotFound):
        discover_files(tmp_path, ".", include=("*.none",))


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not permitted on this platform/account")
    with pytest.raises(UnsafePathError):
        discover_files(tmp_path, "root")


# --------------------------------------------------------------------------- local formats


def test_jsonl_fields_errors_and_bom(tmp_path: Path) -> None:
    payload = (
        b'\xef\xbb\xbf{"id": 1, "text": "first", "url": "https://x.example", "license": "mit", "lang": "en"}\n'
        b"\n"
        b'{"id": 2, "text": "second", "timestamp": 1700000000}\n'
        b"not json\n"
        b"[1, 2]\n"
        b'{"id": 5}\n'
    )
    (tmp_path / "d.jsonl").write_bytes(payload)
    reader = local(
        tmp_path, path="d.jsonl", id_field="id", url_field="url", license_field="license",
        timestamp_field="timestamp", metadata_fields=("lang",),
    )  # fmt: skip
    items = records(reader)
    ok = [r for r in items if isinstance(r, RawRecord)]
    errors = [r for r in items if isinstance(r, RecordError)]
    assert [r.content for r in ok] == ["first", "second"]
    assert ok[0].record_id == "1"
    assert ok[0].license == "mit"
    assert ok[0].extra == {"lang": "en"}
    assert ok[1].timestamp is not None
    assert ok[1].timestamp.startswith("2023-11-14")
    assert ok[0].locator == "d.jsonl#L1"
    assert [e.error_type for e in errors] == ["invalid_json", "not_an_object", "missing_text_field"]


def test_jsonl_decode_policies(tmp_path: Path) -> None:
    (tmp_path / "d.jsonl").write_bytes(b'{"text": "caf\xe9"}\n')
    replaced = records(local(tmp_path, path="d.jsonl"))
    assert isinstance(replaced[0], RawRecord)
    assert replaced[0].decode_replaced
    skipped = records(local(tmp_path, path="d.jsonl", on_decode_error="skip"))
    assert isinstance(skipped[0], RecordError)
    with pytest.raises(DecodingError):
        records(local(tmp_path, path="d.jsonl", on_decode_error="fail"))


def test_jsonl_gzip_and_max_records(tmp_path: Path) -> None:
    with gzip.open(tmp_path / "d.jsonl.gz", "wt", encoding="utf-8") as handle:
        for index in range(10):
            handle.write(json.dumps({"text": f"doc {index}"}) + "\n")
    items = records(local(tmp_path, path="d.jsonl.gz", max_records=3))
    assert [r.content for r in items if isinstance(r, RawRecord)] == ["doc 0", "doc 1", "doc 2"]


def test_json_array_and_errors(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps([{"text": "a"}, 3, {"text": "b"}]), encoding="utf-8")
    items = records(local(tmp_path, path="a.json"))
    assert [type(i).__name__ for i in items] == ["RawRecord", "RecordError", "RawRecord"]
    (tmp_path / "obj.json").write_text('{"text": "a"}', encoding="utf-8")
    with pytest.raises(SourceFormatError, match="top-level JSON array"):
        records(local(tmp_path, path="obj.json"))
    (tmp_path / "big.json").write_text(json.dumps([{"text": "x" * 100}]), encoding="utf-8")
    with pytest.raises(SourceFormatError, match="JSONL"):
        records(local(tmp_path, path="big.json", max_json_bytes=10))


def test_csv_and_tsv(tmp_path: Path) -> None:
    # newline="\n": the reader preserves raw bytes, so keep the fixture platform-independent.
    (tmp_path / "a.csv").write_text('id,text\n1,"hello, world"\n2,"multi\nline"\n', encoding="utf-8", newline="\n")
    (tmp_path / "b.tsv").write_text("id\ttext\n7\ttab separated\n", encoding="utf-8", newline="\n")
    csv_items = records(local(tmp_path, path="a.csv", id_field="id"))
    assert [r.content for r in csv_items if isinstance(r, RawRecord)] == ["hello, world", "multi\nline"]
    tsv_items = records(local(tmp_path, path="b.tsv"))
    assert isinstance(tsv_items[0], RawRecord)
    assert tsv_items[0].content == "tab separated"


def test_parquet_projection_and_missing_column(tmp_path: Path) -> None:
    pq.write_table(pa.table({"id": ["a", "b"], "body": ["x", "y"], "unused": [1, 2]}), tmp_path / "p.parquet")
    items = records(local(tmp_path, path="p.parquet", text_field="body", id_field="id"))
    assert [(r.record_id, r.content) for r in items if isinstance(r, RawRecord)] == [("a", "x"), ("b", "y")]
    with pytest.raises(SourceFormatError, match="no column"):
        records(local(tmp_path, path="p.parquet"))
    (tmp_path / "broken.parquet").write_bytes(b"PAR1 not really parquet")
    with pytest.raises(SourceFormatError):
        records(local(tmp_path, path="broken.parquet", text_field="body"))


def test_text_files_mime_and_size_limit(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# Title\n\nBody", encoding="utf-8")
    (tmp_path / "docs" / "b.html").write_text("<p>Hi</p>", encoding="utf-8")
    (tmp_path / "docs" / "c.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs" / "huge.txt").write_text("x" * 200, encoding="utf-8")
    items = records(local(tmp_path, path="docs", max_file_bytes=100))
    by_locator = {i.locator: i for i in items if isinstance(i, RawRecord)}
    errors = {i.locator: i for i in items if isinstance(i, RecordError)}
    assert by_locator["docs/a.md"].mime_type == "text/markdown"
    assert by_locator["docs/b.html"].mime_type == "text/html"
    assert by_locator["docs/c.py"].programming_language == "python"
    assert errors["docs/huge.txt"].error_type == "file_too_large"


def test_describe_fingerprints_content(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    first = local(tmp_path, path="a.txt").describe()
    assert first.files[0].size == 3
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    second = local(tmp_path, path="a.txt").describe()
    assert first.fingerprint != second.fingerprint


# --------------------------------------------------------------------------- archives


def test_zip_members_are_streamed_in_place(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("b/doc.jsonl", json.dumps({"text": "from jsonl"}) + "\n")
        handle.writestr("a.txt", "plain text member")
    items = records(local(tmp_path, path="bundle.zip"))
    assert [(i.locator, i.content) for i in items if isinstance(i, RawRecord)] == [
        ("bundle.zip!a.txt", "plain text member"),
        ("bundle.zip!b/doc.jsonl#L1", "from jsonl"),
    ]
    assert not (tmp_path / "a.txt").exists()  # nothing was extracted


@pytest.mark.parametrize("name", ["../evil.txt", "/etc/passwd", "C:/windows/x", "a/../../b"])
def test_member_names_cannot_escape(name: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_member_name(name)


def test_zip_slip_member_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../../escape.txt", "x")
    with pytest.raises(UnsafePathError):
        list(iter_archive_members(archive))


def test_decompression_bomb_limits(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("zeros.txt", "0" * 2_000_000)
    members = list(iter_archive_members(archive, ArchiveLimits(max_compression_ratio=50)))
    assert members[0].skipped_reason == "suspicious_compression_ratio"
    members = list(iter_archive_members(archive, ArchiveLimits(max_member_bytes=1000, max_compression_ratio=1e9)))
    assert members[0].skipped_reason == "member_too_large"


def test_tar_symlinks_are_not_followed(tmp_path: Path) -> None:
    archive = tmp_path / "t.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        data = b"hello"
        info = tarfile.TarInfo("doc.txt")
        info.size = len(data)
        handle.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        handle.addfile(link)
    members = {m.name: m for m in iter_archive_members(archive)}
    assert members["doc.txt"].data == b"hello"
    assert members["link.txt"].skipped_reason == "non_regular_member"


def test_corrupted_archive_raises(tmp_path: Path) -> None:
    (tmp_path / "bad.zip").write_bytes(b"PK\x03\x04 not a zip")
    with pytest.raises(SourceFormatError, match="corrupted archive"):
        list(iter_archive_members(tmp_path / "bad.zip"))


# --------------------------------------------------------------------------- code trees


def test_code_source_skips_binaries_vendor_and_non_code(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    (tree / "pkg").mkdir(parents=True)
    (tree / "node_modules").mkdir()
    (tree / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tree / "pkg" / "b.go").write_text("package main\n", encoding="utf-8")
    (tree / "pkg" / "c.bin.py").write_bytes(b"\x00\x01binary")
    (tree / "pkg" / "notes.txt").write_text("not code", encoding="utf-8")
    (tree / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    cfg = CodeSourceConfig(type="code", path="repo", license="MIT")
    items = records(CodeSourceReader(ResolvedSource("code", cfg, tmp_path)))
    ok = sorted(i.locator for i in items if isinstance(i, RawRecord))
    assert ok == ["repo/pkg/a.py", "repo/pkg/b.go"]
    assert [i.error_type for i in items if isinstance(i, RecordError)] == ["binary_file"]


# --------------------------------------------------------------------------- helpers


def test_json_safe_and_timestamps() -> None:
    import datetime as dt

    assert json_safe({"a": float("nan"), "b": b"x", "c": (1, 2)}) == {"a": None, "b": None, "c": [1, 2]}
    assert normalize_timestamp(0) == "1970-01-01T00:00:00+00:00"
    assert normalize_timestamp(dt.date(2024, 1, 2)) == "2024-01-02"
    assert normalize_timestamp("") is None
    assert normalize_timestamp("2024-05") == "2024-05"
