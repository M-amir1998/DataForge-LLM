"""Unbounded deterministic document stream for scale and memory benchmarks.

Documents are generated on the fly (never materialized), so a corpus larger than RAM can
be streamed through the pipeline. Every 40th document repeats an earlier one and every
25th is a light mutation of an earlier one, so dedup stages have real work to do.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from dataforge.synthetic.text import TextGenerator

_DOMAINS: Final = (
    "news",
    "science",
    "technology",
    "legal",
    "general",
    "mathematics",
    "forums",
    "books",
)
_HISTORY: Final = 64


@dataclass(frozen=True, slots=True)
class SyntheticDocument:
    record_id: str
    text: str
    domain: str


def iter_synthetic_documents(
    *,
    seed: int,
    mean_words: int,
    num_documents: int | None = None,
    total_bytes: int | None = None,
) -> Iterator[SyntheticDocument]:
    """Yield documents until ``num_documents`` or ``total_bytes`` (UTF-8) is reached."""
    rng = random.Random(seed)
    generator = TextGenerator(rng)
    history: deque[SyntheticDocument] = deque(maxlen=_HISTORY)
    sentences = max(3, mean_words // 18)
    produced_bytes = 0
    index = 0
    while True:
        if num_documents is not None and index >= num_documents:
            return
        if total_bytes is not None and produced_bytes >= total_bytes:
            return
        record_id = f"syn-{index:010d}"
        if history and index % 40 == 39:
            source = history[rng.randrange(len(history))]
            document = SyntheticDocument(record_id, source.text, source.domain)
        elif history and index % 25 == 24:
            source = history[rng.randrange(len(history))]
            document = SyntheticDocument(record_id, generator.mutate(source.text, edits=2), source.domain)
        else:
            domain = _DOMAINS[rng.randrange(len(_DOMAINS))]
            _, text = generator.document(domain, min_sentences=sentences // 2 + 1, max_sentences=sentences * 3 // 2)
            document = SyntheticDocument(record_id, text, domain)
            history.append(document)
        produced_bytes += len(document.text.encode("utf-8"))
        index += 1
        yield document
