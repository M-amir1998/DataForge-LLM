"""Word tokenization and vectorized n-gram (shingle) hashing.

Shingles are hashed without materializing n-gram strings: each word is hashed once with
xxh3-64, then consecutive word hashes are combined with a polynomial rolling hash in uint64
arithmetic (wraparound is intended) and finally bit-mixed.
"""

from __future__ import annotations

import re
from typing import Final

import numpy as np
import numpy.typing as npt
import xxhash

UInt64Array = npt.NDArray[np.uint64]

_WORD: Final = re.compile(r"\w+")
_NON_WORD: Final = re.compile(r"[\W_]+")
_POLY: Final = np.uint64(0x100000001B3)
_MIX1: Final = np.uint64(0xFF51AFD7ED558CCD)
_MIX2: Final = np.uint64(0xC4CEB9FE1A85EC53)
_SHIFT: Final = np.uint64(33)
_EMPTY: Final = np.empty(0, dtype=np.uint64)


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens (Unicode-aware, punctuation dropped)."""
    return _WORD.findall(text.lower())


def tokenize_with_offsets(text: str) -> tuple[list[str], list[int], list[int]]:
    """Tokens plus their character start/end offsets in ``text``."""
    words: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for match in _WORD.finditer(text):
        words.append(match.group().lower())
        starts.append(match.start())
        ends.append(match.end())
    return words, starts, ends


def normalize_for_matching(text: str) -> str:
    """Lower-case, punctuation-free, single-spaced text used for substring matching."""
    return _NON_WORD.sub(" ", text.lower()).strip()


def word_hashes(words: list[str]) -> UInt64Array:
    if not words:
        return _EMPTY
    digest = xxhash.xxh3_64_intdigest
    return np.fromiter(
        (digest(word.encode("utf-8", "surrogatepass")) for word in words),
        dtype=np.uint64,
        count=len(words),
    )


def _mix(values: UInt64Array) -> UInt64Array:
    values = values ^ (values >> _SHIFT)
    values = values * _MIX1
    values = values ^ (values >> _SHIFT)
    values = values * _MIX2
    return values ^ (values >> _SHIFT)


def ngram_hashes(hashes: UInt64Array, n: int, *, short_as_single: bool = True) -> UInt64Array:
    """Hash every window of ``n`` consecutive word hashes.

    Documents shorter than ``n`` words yield a single shingle covering all words when
    ``short_as_single`` is set (so short texts can still match each other), else nothing.
    """
    count = hashes.shape[0]
    if count == 0:
        return _EMPTY
    if count < n:
        if not short_as_single:
            return _EMPTY
        n = count
    windows = count - n + 1
    combined = np.zeros(windows, dtype=np.uint64)
    with np.errstate(over="ignore"):
        for offset in range(n):
            combined = combined * _POLY + hashes[offset : offset + windows]
        return _mix(combined)


def shingle_hashes(text: str, n: int) -> UInt64Array:
    """Unique hashed word n-grams of ``text`` (order not preserved)."""
    return np.unique(ngram_hashes(word_hashes(tokenize(text)), n))
