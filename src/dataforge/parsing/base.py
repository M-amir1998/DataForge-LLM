"""Parser port: turn a decoded record payload into clean text.

v0.1 ships plain-text/Markdown/code passthrough and HTML extraction. PDF, OCR, or WARC
parsers plug in by registering another :class:`Parser` for their MIME type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from dataforge.parsing.html import html_to_text


@dataclass(frozen=True, slots=True)
class ParsedText:
    text: str
    title: str | None = None
    transformation: str | None = None


class Parser(Protocol):
    name: str

    def parse(self, content: str) -> ParsedText: ...


class PassthroughParser:
    """Text-like payloads (plain text, Markdown, source code) are already text."""

    name = "passthrough"

    def parse(self, content: str) -> ParsedText:
        return ParsedText(content)


class HTMLParser:
    name = "html"

    def parse(self, content: str) -> ParsedText:
        text, title = html_to_text(content)
        return ParsedText(text, title=title, transformation="parse:html")


class ParserRegistry:
    """Selects a parser by MIME type, falling back to passthrough for ``text/*``."""

    def __init__(self, parsers: Mapping[str, Parser] | None = None) -> None:
        self._parsers: dict[str, Parser] = {"text/html": HTMLParser()}
        if parsers:
            self._parsers.update(parsers)
        self._fallback: Parser = PassthroughParser()

    def register(self, mime_type: str, parser: Parser) -> None:
        self._parsers[mime_type] = parser

    def for_mime(self, mime_type: str | None) -> Parser:
        if mime_type is None:
            return self._fallback
        return self._parsers.get(mime_type, self._fallback)

    def parse(self, content: str, mime_type: str | None) -> ParsedText:
        return self.for_mime(mime_type).parse(content)
