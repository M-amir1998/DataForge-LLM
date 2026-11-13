"""Exact, paragraph-level, and near-duplicate detection."""

from dataforge.dedup.bloom import BloomFilter
from dataforge.dedup.exact import ExactDeduplicator, ExactDedupResult
from dataforge.dedup.minhash import MinHasher, jaccard_estimate, optimal_bands
from dataforge.dedup.near import NearDeduplicator, NearDedupResult
from dataforge.dedup.paragraph import ParagraphDeduplicator
from dataforge.dedup.shingles import shingle_hashes, tokenize

__all__ = [
    "BloomFilter",
    "ExactDedupResult",
    "ExactDeduplicator",
    "MinHasher",
    "NearDedupResult",
    "NearDeduplicator",
    "ParagraphDeduplicator",
    "jaccard_estimate",
    "optimal_bands",
    "shingle_hashes",
    "tokenize",
]
