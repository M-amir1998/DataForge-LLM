from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dataforge.config.models import ContaminationConfig
from dataforge.contamination.detector import ContaminationDetector
from dataforge.contamination.registry import load_benchmarks
from dataforge.errors import ConfigError, SourceNotFound
from dataforge.synthetic.text import TextGenerator

QUESTION = (
    "A community garden has 24 raised beds and each bed holds 6 tomato seedlings that later "
    "yield 9 tomatoes. After 40 tomatoes are donated, how many tomatoes remain for the gardeners?"
)
SOLUTION = "    return sum(item * item for item in values if item % 2 == 0)\n"


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    bench = tmp_path / "bench"
    bench.mkdir()
    rows = [
        {"id": "q1", "question": QUESTION, "answer": "1256"},
        {"id": "c1", "question": "def f(values):\n" + SOLUTION, "answer": "x"},
    ]
    (bench / "items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    payload = {
        "benchmarks": {
            "demo": {"path": "bench/items.jsonl", "fields": ["question", "answer"], "license": "CC0-1.0"},
            "absent": {"path": "bench/missing.jsonl", "fields": ["question"], "required": False},
        }
    }
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def detector(registry: Path, **config: object) -> ContaminationDetector:
    cfg = ContaminationConfig(registry=str(registry), **config)  # type: ignore[arg-type]
    probes, _ = load_benchmarks(registry, cfg.benchmarks, cfg.ngram_size)
    return ContaminationDetector(probes, cfg)


def test_registry_loading_skips_optional_missing(registry: Path) -> None:
    probes, infos = load_benchmarks(registry, (), 8)
    by_name = {i.name: i for i in infos}
    assert by_name["demo"].available
    assert by_name["demo"].items == 2
    assert by_name["demo"].short_probes == 2  # one-token answers cannot be n-gram indexed
    assert not by_name["absent"].available
    assert {p.field for p in probes} == {"question", "answer"}


def test_required_missing_and_unknown_benchmarks(registry: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown benchmarks"):
        load_benchmarks(registry, ("nope",), 8)
    payload = {"benchmarks": {"must": {"path": "missing.jsonl", "fields": ["q"]}}}
    path = tmp_path / "strict.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(SourceNotFound):
        load_benchmarks(path, (), 8)


def test_exact_match(registry: Path) -> None:
    matches = detector(registry).scan(QUESTION.upper())
    assert [(m.method, m.item_id, m.field) for m in matches] == [("exact", "q1", "question")]


def test_substring_match_has_offsets_and_no_text(registry: Path) -> None:
    text = "Homework help: " + QUESTION + " Thanks for any hints, I am stuck on the second step."
    match = detector(registry).scan(text)[0]
    assert (match.method, match.score) == ("substring", 1.0)
    start, end = match.spans[0]
    assert text[start:end].startswith("A community garden")
    assert QUESTION not in repr(match)
    assert len(match.evidence_hash) == 16


def test_ngram_partial_overlap(registry: Path) -> None:
    edited = QUESTION.replace("donated", "given away")
    text = "Worked example from class. " + edited
    match = detector(registry).scan(text)[0]
    assert match.method == "ngram"
    assert 0.5 <= match.score < 1.0
    assert match.matched_ngrams < match.total_ngrams


def test_minhash_catches_reworded_whole_document(registry: Path) -> None:
    reworded = QUESTION.replace("donated", "handed out")
    found = detector(registry, methods=("minhash",), minhash_threshold=0.5).scan(reworded)
    assert [m.method for m in found] == ["minhash"]
    assert 0.5 <= found[0].score < 1.0


def test_code_solution_detected_and_unrelated_text_clean(registry: Path) -> None:
    code = "import math\n\ndef compute(values):\n" + SOLUTION + "\nprint(compute([1, 2, 3]))\n"
    assert any(m.item_id == "c1" for m in detector(registry, ngram_size=5).scan(code))
    unrelated = TextGenerator(__import__("random").Random(2)).document("science")[1]
    assert detector(registry).scan(unrelated) == []


def test_methods_can_be_restricted(registry: Path) -> None:
    text = "Homework help: " + QUESTION
    assert [m.method for m in detector(registry, methods=("ngram",)).scan(text)] == ["ngram"]
    assert detector(registry, methods=("exact",)).scan(text) == []
