"""Detected spans, overlap resolution, and placeholder redaction."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open character range ``[start, end)`` flagged by a detector.

    Spans never store the matched value, so they are safe to log and to persist.
    """

    start: int
    end: int
    kind: str
    category: str

    @property
    def length(self) -> int:
        return self.end - self.start


def resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    """Keep a maximal set of non-overlapping spans, preferring earlier then longer matches."""
    ordered = sorted(spans, key=lambda s: (s.start, -s.length, s.kind))
    kept: list[Span] = []
    for span in ordered:
        if kept and span.start < kept[-1].end:
            if span.length > kept[-1].length and span.start == kept[-1].start:
                kept[-1] = span
            continue
        kept.append(span)
    return kept


def placeholder(span: Span) -> str:
    if span.category == "secret":
        return "<SECRET>"
    return f"<{span.kind.upper()}>"


def redact(text: str, spans: Iterable[Span]) -> str:
    """Replace each (non-overlapping) span with its typed placeholder."""
    pieces: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda s: s.start):
        if span.start < cursor:
            continue
        pieces.append(text[cursor : span.start])
        pieces.append(placeholder(span))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)
