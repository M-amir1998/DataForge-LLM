"""Language identification behind a small port.

:class:`BuiltinLanguageDetector` is a transparent, dependency-free baseline: Unicode script
analysis for non-Latin scripts and distinctive function-word profiles for seven Latin-script
languages. It is fast and explainable, but not a substitute for a trained model on short or
mixed-language text; :class:`Py3LangidDetector` (``langid`` extra) is the statistical option.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

import regex

from dataforge.errors import OptionalDependencyMissing

UNDETERMINED: Final = "und"


@dataclass(frozen=True, slots=True)
class LanguagePrediction:
    language: str
    confidence: float


class LanguageDetector(Protocol):
    name: str

    def detect(self, text: str) -> LanguagePrediction: ...


# Distinctive function words only: words shared by several languages ("de", "la", "que",
# "en", "a", "e", "o", "para") are deliberately excluded from every profile.
_PROFILES: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        "the and of to is that with for was are this have from by were which not but they their "
        "has been would will there what about into when after than also because these who its "
        "it be or at as".split()
    ),
    "es": frozenset(
        "el los las y es pero más sus también muy hasta cuando según años hay fue ellos donde "
        "puede hacer ahora entonces aunque mientras nuevo nueva gobierno además dijo".split()
    ),
    "fr": frozenset(
        "le les des et est une dans pour pas qui sur avec sont mais ont aux du au été cette ces "
        "leur nous vous ils elle plus selon très fait être".split()
    ),
    "de": frozenset(
        "der die das und ist nicht mit den dem ein eine sich auf für sind auch von zu wird werden "
        "bei nach aus über dass oder wie noch haben einer eines im".split()
    ),
    "it": frozenset(
        "il gli della delle che è non per sono nel alla anche più questo questa dalla degli loro molto essere".split()
    ),
    "pt": frozenset("os não em são pelo pela também muito seu sua isso foi ao aos nas uma ele ela".split()),
    "nl": frozenset(
        "het een van niet dat op zijn voor met aan ook maar bij worden naar uit wordt deze nog wel".split()
    ),
}

_SCRIPTS: Final[tuple[tuple[str, regex.Pattern[str]], ...]] = (
    ("hangul", regex.compile(r"\p{Hangul}")),
    ("kana", regex.compile(r"[\p{Hiragana}\p{Katakana}]")),
    ("han", regex.compile(r"\p{Han}")),
    ("cyrillic", regex.compile(r"\p{Cyrillic}")),
    ("arabic", regex.compile(r"\p{Arabic}")),
    ("greek", regex.compile(r"\p{Greek}")),
    ("devanagari", regex.compile(r"\p{Devanagari}")),
    ("hebrew", regex.compile(r"\p{Hebrew}")),
    ("thai", regex.compile(r"\p{Thai}")),
    ("latin", regex.compile(r"\p{Latin}")),
)
_WORDS: Final = regex.compile(r"\p{L}+")
_UKRAINIAN: Final = regex.compile(r"[їєіґЇЄІҐ]")
_PERSIAN: Final = regex.compile(r"[پچژگ]")
_MAX_WORDS: Final = 1000
_FULL_COVERAGE_DENSITY: Final = 0.15


def _latin(text: str) -> LanguagePrediction:
    words = [w.lower() for w in _WORDS.findall(text, concurrent=False)[:_MAX_WORDS]]
    if len(words) < 3:
        return LanguagePrediction(UNDETERMINED, 0.0)
    counts = {lang: sum(1 for w in words if w in profile) for lang, profile in _PROFILES.items()}
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    (best, best_count), (_, second_count) = ranked[0], ranked[1]
    if best_count == 0:
        return LanguagePrediction(UNDETERMINED, 0.0)
    separation = (best_count - second_count) / best_count
    coverage = min(1.0, best_count / len(words) / _FULL_COVERAGE_DENSITY)
    return LanguagePrediction(best, round(coverage * (0.5 + 0.5 * separation), 4))


class BuiltinLanguageDetector:
    name = "builtin"

    def __init__(self, sample_chars: int = 5000) -> None:
        self.sample_chars = sample_chars

    def detect(self, text: str) -> LanguagePrediction:
        sample = text[: self.sample_chars]
        if sample.isascii():
            return _latin(sample)
        counts = {name: len(pattern.findall(sample)) for name, pattern in _SCRIPTS}
        letters = sum(counts.values())
        if letters == 0:
            return LanguagePrediction(UNDETERMINED, 0.0)
        script = max(counts, key=lambda name: counts[name])
        share = counts[script] / letters
        if script == "latin":
            prediction = _latin(sample)
            return LanguagePrediction(prediction.language, round(prediction.confidence * share, 4))
        if script in {"han", "kana"}:
            language = "ja" if counts["kana"] > 0.05 * letters else "zh"
            share = (counts["han"] + counts["kana"]) / letters
        else:
            language = {
                "hangul": "ko",
                "cyrillic": "uk" if _UKRAINIAN.search(sample) else "ru",
                "arabic": "fa" if _PERSIAN.search(sample) else "ar",
                "greek": "el",
                "devanagari": "hi",
                "hebrew": "he",
                "thai": "th",
            }[script]
        return LanguagePrediction(language, round(min(1.0, share), 4))


class Py3LangidDetector:
    """Adapter for ``py3langid`` (naive Bayes over byte n-grams, CPU only)."""

    name = "py3langid"

    def __init__(self, sample_chars: int = 5000) -> None:
        try:
            from py3langid.langid import (  # noqa: PLC0415 - optional extra
                MODEL_FILE,
                LanguageIdentifier,
            )
        except ImportError as exc:
            raise OptionalDependencyMissing("py3langid language detection", "langid") from exc
        identifier: Any = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
        self._classify: Callable[[str], tuple[str, float]] = identifier.classify
        self.sample_chars = sample_chars

    def detect(self, text: str) -> LanguagePrediction:
        language, probability = self._classify(text[: self.sample_chars])
        return LanguagePrediction(str(language), round(float(probability), 4))


def build_detector(kind: str, sample_chars: int) -> LanguageDetector:
    if kind == "py3langid":
        return Py3LangidDetector(sample_chars)
    return BuiltinLanguageDetector(sample_chars)
