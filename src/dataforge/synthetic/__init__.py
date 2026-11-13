"""Deterministic synthetic data for demos, tests, and benchmarks."""

from dataforge.synthetic.benchmarks import demo_code_items, demo_math_items, write_demo_benchmarks
from dataforge.synthetic.corpus import CorpusSummary, generate_sample_corpus
from dataforge.synthetic.stream import SyntheticDocument, iter_synthetic_documents

__all__ = [
    "CorpusSummary",
    "SyntheticDocument",
    "demo_code_items",
    "demo_math_items",
    "generate_sample_corpus",
    "iter_synthetic_documents",
    "write_demo_benchmarks",
]
