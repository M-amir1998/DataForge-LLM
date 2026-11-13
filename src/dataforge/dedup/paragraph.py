"""Corpus-level paragraph deduplication with a fixed-memory Bloom filter.

Removes paragraphs already seen earlier in the stream (boilerplate footers, syndicated
blocks) while keeping each document's first occurrences. Order-dependent by design, so the
stage is stateful and snapshots its filter at every checkpoint. Bloom false positives
remove a unique paragraph with probability ``error_rate``; that trade-off buys a hard
memory bound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dataforge.config.models import ParagraphDedupConfig
from dataforge.dedup.bloom import BloomFilter

_PARAGRAPH_BREAK: Final = re.compile(r"\n\s*\n")
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ParagraphDedupOutcome:
    text: str
    removed: int
    removed_chars: int


class ParagraphDeduplicator:
    def __init__(self, config: ParagraphDedupConfig) -> None:
        self.config = config
        self.filter = BloomFilter(config.capacity, config.error_rate)

    def process(self, text: str) -> ParagraphDedupOutcome:
        kept: list[str] = []
        removed = 0
        removed_chars = 0
        for paragraph in _PARAGRAPH_BREAK.split(text):
            if len(paragraph.strip()) < self.config.min_chars:
                kept.append(paragraph)
                continue
            key = _SPACES.sub(" ", paragraph.strip().lower()).encode("utf-8", "surrogatepass")
            if self.filter.add(key):
                removed += 1
                removed_chars += len(paragraph)
            else:
                kept.append(paragraph)
        return ParagraphDedupOutcome("\n\n".join(kept).strip(), removed, removed_chars)

    def save(self, path: Path) -> None:
        self.filter.save(path)

    def load(self, path: Path) -> None:
        self.filter.load(path)
