from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataforge.config.loader import ResolvedSource
from dataforge.config.sources import HuggingFaceSourceConfig, SyntheticSourceConfig
from dataforge.errors import OptionalDependencyMissing
from dataforge.sources.base import RawRecord
from dataforge.sources.huggingface import HuggingFaceSourceReader
from dataforge.sources.synthetic import SyntheticSourceReader
from dataforge.synthetic.corpus import generate_sample_corpus
from dataforge.synthetic.stream import iter_synthetic_documents


def test_stream_is_deterministic_and_bounded() -> None:
    first = [d.text for d in iter_synthetic_documents(seed=3, mean_words=120, num_documents=80)]
    second = [d.text for d in iter_synthetic_documents(seed=3, mean_words=120, num_documents=80)]
    assert first == second
    assert len(first) == 80
    assert len(set(first)) < len(first)  # planted exact repeats
    by_bytes = list(iter_synthetic_documents(seed=1, mean_words=100, total_bytes=20_000))
    total = sum(len(d.text.encode()) for d in by_bytes)
    assert 20_000 <= total < 20_000 + 5_000


def test_synthetic_source_reader() -> None:
    config = SyntheticSourceConfig(type="synthetic", num_documents=25, seed=4, max_records=10)
    reader = SyntheticSourceReader(ResolvedSource("syn", config, Path()))
    records = [r for r in reader.iter_records() if isinstance(r, RawRecord)]
    assert len(records) == 10
    assert records[0].record_id == "syn-0000000000"
    description = reader.describe()
    assert description.reproducible
    assert (
        description.fingerprint == SyntheticSourceReader(ResolvedSource("syn", config, Path())).describe().fingerprint
    )
    with pytest.raises(ValueError, match="exactly one"):
        SyntheticSourceConfig(type="synthetic")


def test_huggingface_reader_offline_behaviour() -> None:
    config = HuggingFaceSourceConfig(type="huggingface", dataset="org/name", split="train", text_field="text")
    reader = HuggingFaceSourceReader(ResolvedSource("hf", config, Path()))
    description = reader.describe()
    assert not description.reproducible
    assert "not pinned" in description.notes[0]
    assert reader.uri == "hf://org/name@unpinned/train"
    try:
        import datasets  # noqa: F401
    except ImportError:
        with pytest.raises(OptionalDependencyMissing, match="hf"):
            list(reader.iter_records())


def test_sample_corpus_is_deterministic(tmp_path: Path) -> None:
    a = generate_sample_corpus(tmp_path / "a", seed=5, scale=0.05)
    b = generate_sample_corpus(tmp_path / "b", seed=5, scale=0.05)
    for relative in ("raw/web.jsonl", "raw/forum.csv", "raw/misc.json", "ground_truth.jsonl"):
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes()
    assert a.phenomena == b.phenomena
    truth = [
        json.loads(line) for line in (tmp_path / "a" / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert sum(1 for t in truth if t["secrets"]) >= 2
    assert all(t["near_duplicate_jaccard"] is None or 0 < t["near_duplicate_jaccard"] <= 1 for t in truth)
    with pytest.raises(ValueError, match="positive"):
        generate_sample_corpus(tmp_path / "c", scale=0)
