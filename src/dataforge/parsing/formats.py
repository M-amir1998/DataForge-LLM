"""File-extension tables: record formats, MIME types, and programming languages."""

from __future__ import annotations

from pathlib import PurePath
from typing import Final, Literal

RecordFormat = Literal["text", "jsonl", "json", "csv", "parquet"]

_FORMAT_BY_SUFFIX: Final[dict[str, RecordFormat]] = {
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
    ".csv": "csv",
    ".tsv": "csv",
    ".parquet": "parquet",
}

#: Extension -> programming language for code detection and provenance.
CODE_LANGUAGES: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".m": "objective-c",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".r": "r",
    ".jl": "julia",
    ".lua": "lua",
    ".pl": "perl",
    ".hs": "haskell",
    ".ex": "elixir",
    ".erl": "erlang",
    ".dart": "dart",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".dockerfile": "dockerfile",
    ".tf": "hcl",
    ".proto": "protobuf",
}

_TEXT_MIME_BY_SUFFIX: Final[dict[str, str]] = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "text/html",
    ".tex": "text/x-tex",
}

_SPECIAL_FILENAMES: Final[dict[str, str]] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "jenkinsfile": "groovy",
}


def _suffix(path: PurePath) -> str:
    suffixes = [s.lower() for s in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return suffixes[-1] if suffixes else ""


def record_format(path: PurePath) -> RecordFormat:
    """Infer the record format of a file (``.gz`` is transparently ignored)."""
    return _FORMAT_BY_SUFFIX.get(_suffix(path), "text")


def programming_language(path: PurePath) -> str | None:
    special = _SPECIAL_FILENAMES.get(path.name.lower())
    if special is not None:
        return special
    return CODE_LANGUAGES.get(_suffix(path))


def mime_type(path: PurePath) -> str:
    language = programming_language(path)
    if language is not None:
        return f"text/x-{language}"
    return _TEXT_MIME_BY_SUFFIX.get(_suffix(path), "text/plain")


def is_gzip(path: PurePath) -> bool:
    return path.suffix.lower() == ".gz"
