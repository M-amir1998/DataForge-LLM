from __future__ import annotations

import sys
import types
from collections.abc import Sequence

import pytest

from dataforge.config.models import (
    LearnedQualityModelConfig,
    LicenseConfig,
    QualityConfig,
    QualitySignalsConfig,
)
from dataforge.domain.document import UNKNOWN_LICENSE, ContentKind
from dataforge.errors import ConfigError, UnsafeLicense
from dataforge.licensing.policy import LicensePolicy
from dataforge.licensing.spdx import is_known_spdx, normalize_license
from dataforge.quality.scorer import QualityScorer
from dataforge.quality.signals import TextStats, build_signals
from tests.support import make_document

PROSE = (
    "The research team measured nitrogen uptake across forty sites to understand soil health. "
    "Their results show a consistent shift that was larger than expected in dry regions.\n\n"
    "Earlier studies suggested that the effect weakens at higher altitudes, but the samples were small. "
    "The authors argue that the new method offers a cheaper way to monitor changes over time."
)


# --------------------------------------------------------------------------- licensing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mit", "MIT"),
        ("Apache License 2.0", "Apache-2.0"),
        ("  apache-2.0 ", "Apache-2.0"),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("public domain", "LicenseRef-PublicDomain"),
        (None, UNKNOWN_LICENSE),
        ("", UNKNOWN_LICENSE),
        ("NOASSERTION", UNKNOWN_LICENSE),
        ("Some Custom Terms", "Some Custom Terms"),
    ],
)
def test_normalize_license(raw: str | None, expected: str) -> None:
    assert normalize_license(raw) == expected


def test_known_spdx() -> None:
    assert is_known_spdx("gpl-3.0-only")
    assert not is_known_spdx("proprietary")


def test_license_policy_decisions() -> None:
    policy = LicensePolicy(LicenseConfig(allowed=("MIT", "apache 2.0"), denied=("GPL-3.0-only",)))
    assert policy.evaluate("MIT").allowed
    assert policy.evaluate("Apache-2.0").allowed
    assert policy.evaluate("GPL-3.0-only").reason == "denied_license"
    assert policy.evaluate("BSD-3-Clause").reason == "license_not_allowed"
    assert policy.evaluate(UNKNOWN_LICENSE).reason == "unknown_license"
    assert not policy.is_included(UNKNOWN_LICENSE)


def test_license_policy_unknown_allow_fail_and_usage() -> None:
    assert LicensePolicy(LicenseConfig(unknown_policy="allow")).evaluate(UNKNOWN_LICENSE).allowed
    with pytest.raises(UnsafeLicense):
        LicensePolicy(LicenseConfig(unknown_policy="fail")).evaluate(UNKNOWN_LICENSE)
    with pytest.raises(UnsafeLicense):
        LicensePolicy(LicenseConfig(disallowed_policy="fail")).evaluate("proprietary")
    usage = LicensePolicy(LicenseConfig(allowed_usage_policies=("commercial",)))
    assert usage.evaluate("MIT", "research-only").reason == "usage_policy_not_allowed"
    assert usage.evaluate("MIT", "commercial").allowed


# --------------------------------------------------------------------------- quality signals


def signal_results(text: str, kind: ContentKind = ContentKind.TEXT, **doc: object) -> dict[str, tuple[bool, bool]]:
    document = make_document(text, kind=kind, **doc)  # type: ignore[arg-type]
    stats = TextStats(text)
    return {
        s.name: (r.applicable, r.passed)
        for s in build_signals(QualitySignalsConfig())
        for r in [s.score(stats, document)]
    }


def test_clean_prose_passes_every_applicable_signal() -> None:
    document = make_document(PROSE)
    document.metadata.language_confidence = 0.95
    assessment = QualityScorer(QualityConfig()).assess(document)
    assert assessment.passed, assessment.failed
    assert assessment.score == 1.0


@pytest.mark.parametrize(
    ("text", "failing"),
    [
        ("Too short.", "length"),
        ("Buy cheap watches now best price " * 40, "ngram_repetition"),
        (("Same line repeated here.\n" * 20) + PROSE, "line_repetition"),
        ("### $$$ ### !!! " * 40 + PROSE, "symbol_ratio"),
        (" ".join(f"https://spam.example.net/{i}" for i in range(60)) + " " + PROSE, "url_ratio"),
        (
            "Home | About | Privacy Policy\nAccept all cookies\nSubscribe to our newsletter\nSign in\n" + PROSE,
            "boilerplate",
        ),
        ("Lorem ipsum dolor sit amet. " + PROSE, "boilerplate"),
        ("aaaa bbbb aaaa bbbb " * 30, "char_entropy"),
        (" ".join(["supercalifragilisticexpialidocious"] * 60), "mean_word_length"),
        ("1234 5678 9012 3456 7890 " * 20 + "the and of", "alpha_words"),
        (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma " * 3,
            "stopwords",
        ),
        ("this line has words but no punctuation at all\n" * 12, "terminal_punctuation"),
    ],
)
def test_each_prose_signal_can_fail(text: str, failing: str) -> None:
    results = signal_results(text)
    applicable, passed = results[failing]
    assert applicable
    assert not passed


def test_code_signals_apply_only_to_code() -> None:
    code = "def f(x):\n    return x * 2\n" * 10
    results = signal_results(code, kind=ContentKind.CODE)
    assert results["terminal_punctuation"][0] is False
    assert results["code_line_length"] == (True, True)
    minified = "var a=" + "1," * 800 + "2;"
    assert signal_results(minified, kind=ContentKind.CODE)["code_line_length"] == (True, False)
    symbols = "{[(<>)]};" * 100
    assert signal_results(symbols, kind=ContentKind.CODE)["alnum_ratio"] == (True, False)


def test_language_dependent_signals() -> None:
    results = signal_results(PROSE, language="de")
    assert results["stopwords"][0] is False
    assert signal_results(PROSE, language="zh")["mean_word_length"][0] is False
    assert signal_results(PROSE)["language_confidence"][0] is False  # no confidence annotated


def test_min_score_policy_and_rejection_reason() -> None:
    lenient = QualityScorer(QualityConfig(combine="min_score", min_score=0.5))
    strict = QualityScorer(QualityConfig())
    document = make_document("Lorem ipsum dolor sit amet. " + PROSE)
    assert lenient.assess(document).passed
    assessment = strict.assess(document)
    assert not assessment.passed
    assert assessment.rejection_reason == "boilerplate"
    assert 0.0 < assessment.score < 1.0


def test_disabled_signals_are_not_built() -> None:
    config = QualitySignalsConfig.model_validate({"length": {"enabled": False}})
    assert "length" not in {s.name for s in build_signals(config)}


class _FakeModel:
    def predict(self, texts: Sequence[str]) -> list[float]:
        return [0.9 if "research" in t else 0.1 for t in texts]


def test_learned_model_plug_in(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake_quality_model")
    module.build = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_quality_model", module)
    config = QualityConfig(
        learned_model=LearnedQualityModelConfig(entrypoint="fake_quality_model:build", threshold=0.5)
    )
    scorer = QualityScorer(config)
    good, bad = make_document(PROSE), make_document(PROSE.replace("research", "survey"))
    results = scorer.assess_batch([good, bad])
    assert results[0].passed
    assert results[1].rejection_reason == "learned_model"


def test_learned_model_entrypoint_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="cannot import"):
        QualityScorer(QualityConfig(learned_model=LearnedQualityModelConfig(entrypoint="no_such_module_x:build")))
    module = types.ModuleType("fake_bad_model")
    module.build = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_bad_model", module)
    with pytest.raises(ConfigError, match="predict"):
        QualityScorer(QualityConfig(learned_model=LearnedQualityModelConfig(entrypoint="fake_bad_model:build")))
