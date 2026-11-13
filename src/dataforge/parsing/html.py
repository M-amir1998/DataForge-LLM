"""Dependency-free HTML to text extraction.

Deliberately conservative: it removes markup, scripts, and styles, keeps block structure as
paragraphs, and preserves ``<pre>`` content verbatim. It is not a boilerplate remover; that
is the quality stage's job, where the decision is measurable.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Final

from typing_extensions import override

_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head"})
# fmt: off
_BLOCK_TAGS: Final = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
        "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
        "thead", "tr", "ul",
    }
)
# fmt: on
_INLINE_WS: Final = re.compile(r"[ \t\r\n\f\v]+")
_BLANK_RUNS: Final = re.compile(r"\n{3,}")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._in_title = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n\n" if tag not in {"br", "li", "tr", "dd", "dt"} else "\n")

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "hr"}:
            self.chunks.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n\n")

    @override
    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._pre_depth:
            self.chunks.append(data)
        else:
            self.chunks.append(_INLINE_WS.sub(" ", data))


def html_to_text(html: str) -> tuple[str, str | None]:
    """Return ``(text, title)`` extracted from an HTML document."""
    extractor = _Extractor()
    extractor.feed(html)
    extractor.close()
    raw = "".join(extractor.chunks)
    lines = [line.strip() if line.strip() else "" for line in raw.split("\n")]
    text = _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()
    title = _INLINE_WS.sub(" ", "".join(extractor.title_parts)).strip() or None
    return text, title
