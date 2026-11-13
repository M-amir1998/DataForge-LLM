from __future__ import annotations

import random
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from dataforge.config.models import MixtureConfig, SplitConfig, TokenizerConfig
from dataforge.domain.document import Document
from dataforge.errors import OptionalDependencyMissing
from dataforge.mixtures.builder import MixtureBuilder
from dataforge.splitting.splitter import Splitter, parse_timestamp
from dataforge.tokenization.estimators import CharTokenEstimator, WordTokenEstimator, build_estimator
from tests.support import REPO_ROOT, make_document


def corpus(seed: int = 0) -> list[Document]:
    rng = random.Random(seed)
    docs: list[Document] = []
    for domain, count, size in (("general", 300, 400), ("code", 80, 900), ("science", 150, 300)):
        docs.extend(make_document("w " * rng.randint(size // 2, size), domain=domain) for _ in range(count))
    rng.shuffle(docs)
    return docs


def build(config: MixtureConfig, docs: list[Document]) -> tuple[npt.NDArray[np.bool_], dict[str, Any]]:
    builder = MixtureBuilder(config, CharTokenEstimator())
    builder.observe(docs[: len(docs) // 2])
    builder.observe(docs[len(docs) // 2 :])
    keep, plan = builder.finalize()
    return keep, plan.as_dict()


@pytest.mark.parametrize("unit", ["documents", "bytes", "tokens"])
def test_mixture_hits_target_weights(unit: str) -> None:
    config = MixtureConfig(enabled=True, unit=unit, weights={"general": 0.5, "code": 0.3, "science": 0.2})  # type: ignore[arg-type]
    keep, plan = build(config, corpus())
    achieved = {d["domain"]: d["achieved_weight"] for d in plan["domains"]}
    for domain, target in config.weights.items():
        assert achieved[domain] == pytest.approx(target, abs=0.02)
    assert keep.sum() == sum(d["selected_documents"] for d in plan["domains"])
    assert all(d["sampling_rate"] <= 1.0 for d in plan["domains"])


def test_mixture_selection_is_deterministic_and_order_independent() -> None:
    config = MixtureConfig(enabled=True, unit="bytes", weights={"general": 0.6, "code": 0.4})
    docs = corpus(1)
    keep_a, _ = build(config, docs)
    selected_a = {d.document_id for d, k in zip(docs, keep_a, strict=True) if k}
    shuffled = list(docs)
    random.Random(5).shuffle(shuffled)
    keep_b, _ = build(config, shuffled)
    selected_b = {d.document_id for d, k in zip(shuffled, keep_b, strict=True) if k}
    assert selected_a == selected_b


def test_mixture_unlisted_and_missing_domains() -> None:
    docs = corpus(2)
    drop, plan = build(MixtureConfig(enabled=True, unit="documents", weights={"general": 0.5, "code": 0.5}), docs)
    assert plan["unlisted_documents"] == 150
    keep_unlisted, _ = build(
        MixtureConfig(enabled=True, unit="documents", weights={"general": 0.5, "code": 0.5}, unlisted_domains="keep"),
        docs,
    )
    assert keep_unlisted.sum() == drop.sum() + 150
    _, missing = build(MixtureConfig(enabled=True, unit="documents", weights={"general": 0.5, "legal": 0.5}), docs)
    assert any("renormalized" in note for note in missing["notes"])
    assert {d["domain"]: d["achieved_weight"] for d in missing["domains"]}["general"] == 1.0


def test_mixture_target_total_is_capped() -> None:
    config = MixtureConfig(enabled=True, unit="documents", weights={"general": 0.5, "code": 0.5}, target_total=10_000)
    _, plan = build(config, corpus(3))
    assert any("exceeds" in note for note in plan["notes"])
    small = MixtureConfig(enabled=True, unit="documents", weights={"general": 0.5, "code": 0.5}, target_total=40)
    keep, _ = build(small, corpus(3))
    assert keep.sum() == 40


# --------------------------------------------------------------------------- splits


def test_hash_split_is_deterministic_with_expected_ratios() -> None:
    splitter = Splitter(SplitConfig(ratios={"train": 0.8, "validation": 0.1, "test": 0.1}))
    docs = [make_document(f"document number {i}") for i in range(4000)]
    first = [splitter.assign(d) for d in docs]
    again = [Splitter(SplitConfig(ratios={"train": 0.8, "validation": 0.1, "test": 0.1})).assign(d) for d in docs]
    assert first == again
    share = first.count("train") / len(first)
    assert 0.77 < share < 0.83
    other_seed = Splitter(SplitConfig(ratios={"train": 0.8, "validation": 0.1, "test": 0.1}, seed=9))
    assert [other_seed.assign(d) for d in docs] != first


def test_duplicate_groups_never_straddle_splits() -> None:
    splitter = Splitter(SplitConfig(ratios={"train": 0.5, "validation": 0.25, "test": 0.25}))
    for index in range(200):
        members = [make_document(f"variant {index} {j}") for j in range(4)]
        for member in members:
            member.metadata.near_dup_cluster = f"nd-{index}"
        assert len({splitter.assign(m) for m in members}) == 1
    twins = [make_document("identical text"), make_document("identical text")]
    assert splitter.assign(twins[0]) == splitter.assign(twins[1])


def test_source_and_domain_strategies_with_overrides() -> None:
    source = Splitter(SplitConfig(strategy="source", ratios={"train": 0.5, "test": 0.5}, assignments={"held": "test"}))
    assert source.assign(make_document("a", source_id="held")) == "test"
    assert len({source.assign(make_document(str(i), source_id="web")) for i in range(50)}) == 1
    domain = Splitter(SplitConfig(strategy="domain", ratios={"train": 0.5, "test": 0.5}))
    assert len({domain.assign(make_document(str(i), domain="code")) for i in range(50)}) == 1


def test_time_strategy_uses_group_earliest_timestamp() -> None:
    config = SplitConfig(
        strategy="time",
        ratios={"train": 0.8, "validation": 0.1, "test": 0.1},
        time_cutoffs={"validation": "2024-01-01", "test": "2025-01-01"},
    )
    splitter = Splitter(config)
    old = make_document("story", timestamp="2023-05-01T00:00:00Z")
    late_copy = make_document("story", timestamp="2025-06-01T00:00:00Z")
    newer = make_document("new", timestamp="2024-06-01")
    unknown = make_document("no time")
    splitter.observe([old, late_copy, newer, unknown])
    assert splitter.assign(late_copy) == "train"  # shares the content-hash group with the 2023 copy
    assert splitter.assign(newer) == "validation"
    assert splitter.assign(unknown) == "train"
    assert splitter.needs_observation
    restored = Splitter(config)
    restored.set_group_times(splitter.group_times())
    assert restored.assign(newer) == "validation"


def test_parse_timestamp() -> None:
    assert parse_timestamp("2024-01-02T03:04:05Z") is not None
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None


# --------------------------------------------------------------------------- tokens


def test_token_estimators() -> None:
    assert CharTokenEstimator(4.0).estimate("abcdefgh") == 2
    assert WordTokenEstimator(1.5).estimate_batch(["a b", "a b c d"]) == [3, 6]
    assert build_estimator(TokenizerConfig(kind="words"), REPO_ROOT).name.startswith("words")


def test_hf_tokenizer_adapter_is_optional(tmp_path: object) -> None:
    try:
        import tokenizers  # noqa: F401
    except ImportError:
        with pytest.raises(OptionalDependencyMissing, match="tokenizers"):
            build_estimator(TokenizerConfig(kind="hf", tokenizer_path="tok.json"), REPO_ROOT)
