from __future__ import annotations

import codecs
from pathlib import PurePosixPath

import pytest

from dataforge.parsing.base import ParsedText, ParserRegistry
from dataforge.parsing.encoding import decode_bytes
from dataforge.parsing.formats import is_gzip, mime_type, programming_language, record_format
from dataforge.parsing.html import html_to_text

CAFE = "caf\N{LATIN SMALL LETTER E WITH ACUTE}"


def test_decode_utf8_and_boms() -> None:
    assert decode_bytes(CAFE.encode()).text == CAFE
    assert decode_bytes(codecs.BOM_UTF8 + b"hi").text == "hi"
    utf16 = decode_bytes(CAFE.encode("utf-16"))
    assert utf16.text == CAFE
    assert utf16.encoding == "utf-16"
    assert decode_bytes(codecs.BOM_UTF32_LE + "x".encode("utf-32-le")).text == "x"


def test_nearly_valid_utf8_keeps_valid_characters() -> None:
    payload = ("A long sentence with valid text " * 20 + CAFE).encode() + b"\xff end"
    decoded = decode_bytes(payload)
    assert decoded.replaced
    assert CAFE in decoded.text
    assert "\N{REPLACEMENT CHARACTER}" in decoded.text


def test_legacy_encoding_detected() -> None:
    phrase = (
        "Les \N{LATIN SMALL LETTER E WITH ACUTE}l\N{LATIN SMALL LETTER E WITH GRAVE}ves "
        "fran\N{LATIN SMALL LETTER C WITH CEDILLA}ais "
    )
    text = phrase * 30
    decoded = decode_bytes(text.encode("cp1252"))
    assert not decoded.replaced
    assert "fran\N{LATIN SMALL LETTER C WITH CEDILLA}ais" in decoded.text


def test_strict_mode_raises_on_undecodable_bytes() -> None:
    with pytest.raises(UnicodeDecodeError):
        decode_bytes(b"ok \xff", "utf-8", "strict")
    assert decode_bytes(b"ok \xff", "utf-8", "replace").replaced


def test_html_extraction_drops_scripts_keeps_structure() -> None:
    html = """<html><head><title> My  Page </title><style>p{}</style></head>
    <body><nav>Home</nav><script>var x = "<p>";</script>
    <h1>Heading</h1><p>First   paragraph
    continues.</p><p>Second &amp; last.</p><pre>  keep   spacing\n  here</pre></body></html>"""
    text, title = html_to_text(html)
    assert title == "My Page"
    assert "var x" not in text
    assert "Heading\n\nFirst paragraph continues.\n\nSecond & last." in text
    assert "  keep   spacing" in text


def test_parser_registry_dispatch() -> None:
    registry = ParserRegistry()
    assert registry.parse("<p>hi</p>", "text/html").text == "hi"
    assert registry.parse("plain", "text/markdown") == ParsedText("plain")
    assert registry.for_mime(None).name == "passthrough"

    class Upper:
        name = "upper"

        def parse(self, content: str) -> ParsedText:
            return ParsedText(content.upper(), transformation="parse:upper")

    registry.register("text/x-custom", Upper())
    assert registry.parse("abc", "text/x-custom").text == "ABC"


@pytest.mark.parametrize(
    ("name", "fmt", "mime", "language"),
    [
        ("a/b.jsonl", "jsonl", "text/plain", None),
        ("a/b.jsonl.gz", "jsonl", "text/plain", None),
        ("x.parquet", "parquet", "text/plain", None),
        ("x.tsv", "csv", "text/plain", None),
        ("main.py", "text", "text/x-python", "python"),
        ("Dockerfile", "text", "text/x-dockerfile", "dockerfile"),
        ("index.html", "text", "text/html", None),
        ("README.md", "text", "text/markdown", None),
    ],
)
def test_format_tables(name: str, fmt: str, mime: str, language: str | None) -> None:
    path = PurePosixPath(name)
    assert record_format(path) == fmt
    assert mime_type(path) == mime
    assert programming_language(path) == language


def test_is_gzip() -> None:
    assert is_gzip(PurePosixPath("a.jsonl.gz"))
    assert not is_gzip(PurePosixPath("a.jsonl"))
