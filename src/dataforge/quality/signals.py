"""Individual heuristic quality signals.

Each signal measures one interpretable property, compares it with a configured threshold,
and declares which content kinds it applies to (prose heuristics are meaningless for code
and vice versa). Thresholds follow published web-corpus filters (Gopher, C4, The Stack)
as starting points; they are heuristics, not a model of human judgement.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Final, Generic, TypeVar

from typing_extensions import override

from dataforge.config.models import (
    AlnumRatioSignalConfig,
    AlphaWordsSignalConfig,
    BoilerplateSignalConfig,
    CharEntropySignalConfig,
    CodeLineLengthSignalConfig,
    LanguageConfidenceSignalConfig,
    LengthSignalConfig,
    LineRepetitionSignalConfig,
    MeanWordLengthSignalConfig,
    NgramRepetitionSignalConfig,
    QualitySignalsConfig,
    SentenceLengthSignalConfig,
    SignalConfig,
    StopwordSignalConfig,
    SymbolRatioSignalConfig,
    TerminalPunctuationSignalConfig,
    UrlRatioSignalConfig,
)
from dataforge.domain.document import ContentKind, Document

ConfigT = TypeVar("ConfigT", bound=SignalConfig)

TEXT_KINDS: Final = frozenset({ContentKind.TEXT, ContentKind.MIXED})
ALL_KINDS: Final = frozenset(ContentKind)
_WORD: Final = re.compile(r"\w+")
_URL: Final = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[.!?])\s+")
_TERMINAL: Final = (".", "!", "?", '"', "\u201d", "'", ")", ":", "\u2026")
_NO_SPACE_LANGUAGES: Final = frozenset({"zh", "ja", "ko", "th"})
_GOPHER_STOPWORDS: Final = frozenset({"the", "be", "to", "of", "and", "that", "have", "with"})
_BOILERPLATE: Final = re.compile(
    r"cookie|privacy policy|terms of (?:use|service)|all rights reserved|subscribe|newsletter|"
    r"sign (?:in|up)|log ?in|register|click here|read more|share on|javascript|"
    r"accept all|lorem ipsum|\bhome\s*\|",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QualitySignal:
    """Outcome of one heuristic for one document."""

    name: str
    value: float
    passed: bool
    applicable: bool = True
    detail: dict[str, float] = field(default_factory=dict)


class TextStats:
    """Lazily computed views of a document shared by all signals (compute once, reuse)."""

    def __init__(self, text: str) -> None:
        self.text = text

    @cached_property
    def tokens(self) -> list[str]:
        """Whitespace-delimited tokens (punctuation attached)."""
        return self.text.split()

    @cached_property
    def words(self) -> list[str]:
        """Lower-cased word characters only."""
        return _WORD.findall(self.text.lower())

    @cached_property
    def lines(self) -> list[str]:
        return [line.strip() for line in self.text.splitlines() if line.strip()]

    @cached_property
    def raw_lines(self) -> list[str]:
        return self.text.splitlines() or [""]


class Signal(ABC, Generic[ConfigT]):
    name: str = ""
    applies_to: frozenset[ContentKind] = ALL_KINDS

    def __init__(self, config: ConfigT) -> None:
        self.config = config

    @property
    def weight(self) -> float:
        return self.config.weight

    def applicable(self, document: Document) -> bool:
        return document.metadata.content_kind in self.applies_to

    @abstractmethod
    def measure(self, stats: TextStats, document: Document) -> QualitySignal: ...

    def score(self, stats: TextStats, document: Document) -> QualitySignal:
        if not self.applicable(document):
            return QualitySignal(self.name, math.nan, True, applicable=False)
        return self.measure(stats, document)


class LengthSignal(Signal[LengthSignalConfig]):
    name = "length"

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        chars = len(stats.text)
        words = len(stats.words)
        passed = cfg.min_chars <= chars <= cfg.max_chars and words >= cfg.min_words
        return QualitySignal(self.name, float(chars), passed, detail={"words": float(words)})


class MeanWordLengthSignal(Signal[MeanWordLengthSignalConfig]):
    name = "mean_word_length"
    applies_to = TEXT_KINDS

    @override
    def applicable(self, document: Document) -> bool:
        return super().applicable(document) and document.language not in _NO_SPACE_LANGUAGES

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        words = stats.words
        mean = sum(len(w) for w in words) / len(words) if words else 0.0
        return QualitySignal(self.name, mean, cfg.min <= mean <= cfg.max)


class AlphaWordsSignal(Signal[AlphaWordsSignalConfig]):
    name = "alpha_words"
    applies_to = TEXT_KINDS

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        tokens = stats.tokens
        ratio = sum(1 for t in tokens if any(c.isalpha() for c in t)) / len(tokens) if tokens else 0.0
        return QualitySignal(self.name, ratio, ratio >= cfg.min_ratio)


class SymbolRatioSignal(Signal[SymbolRatioSignalConfig]):
    name = "symbol_ratio"
    applies_to = TEXT_KINDS

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        tokens = stats.tokens
        symbols = stats.text.count("#") + stats.text.count("...") + stats.text.count("\u2026")
        ratio = symbols / len(tokens) if tokens else 0.0
        return QualitySignal(self.name, ratio, ratio <= cfg.max_ratio)


class LineRepetitionSignal(Signal[LineRepetitionSignalConfig]):
    name = "line_repetition"
    applies_to = TEXT_KINDS

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        lines = stats.lines
        if not lines:
            return QualitySignal(self.name, 0.0, True)
        seen: set[str] = set()
        dup_lines = dup_chars = 0
        for line in lines:
            if line in seen:
                dup_lines += 1
                dup_chars += len(line)
            else:
                seen.add(line)
        line_fraction = dup_lines / len(lines)
        char_fraction = dup_chars / max(1, sum(len(line) for line in lines))
        passed = line_fraction <= cfg.max_duplicate_line_fraction and char_fraction <= cfg.max_duplicate_char_fraction
        return QualitySignal(self.name, line_fraction, passed, detail={"char_fraction": char_fraction})


class NgramRepetitionSignal(Signal[NgramRepetitionSignalConfig]):
    name = "ngram_repetition"
    applies_to = TEXT_KINDS

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        words = stats.words
        n = cfg.n
        if len(words) < n + 1:
            return QualitySignal(self.name, 0.0, True)
        grams = Counter(tuple(words[i : i + n]) for i in range(len(words) - n + 1))
        total = sum(grams.values())
        repeated = sum(count - 1 for count in grams.values() if count > 1)
        ratio = repeated / total
        return QualitySignal(self.name, ratio, ratio <= cfg.max_ratio)


class CharEntropySignal(Signal[CharEntropySignalConfig]):
    name = "char_entropy"

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        text = stats.text
        if len(text) < cfg.min_chars:
            return QualitySignal(self.name, math.nan, True, applicable=False)
        counts = Counter(text)
        length = len(text)
        entropy = -sum(c / length * math.log2(c / length) for c in counts.values())
        return QualitySignal(self.name, entropy, entropy >= cfg.min_bits)


class UrlRatioSignal(Signal[UrlRatioSignalConfig]):
    name = "url_ratio"
    applies_to = TEXT_KINDS

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        text = stats.text
        url_chars = sum(len(m.group()) for m in _URL.finditer(text))
        ratio = url_chars / len(text) if text else 0.0
        return QualitySignal(self.name, ratio, ratio <= cfg.max_ratio)


class BoilerplateSignal(Signal[BoilerplateSignalConfig]):
    name = "boilerplate"
    applies_to = TEXT_KINDS

    def __init__(self, config: BoilerplateSignalConfig) -> None:
        super().__init__(config)
        extra = "|".join(f"(?:{p})" for p in config.extra_patterns)
        self._pattern = re.compile(f"{_BOILERPLATE.pattern}|{extra}", re.IGNORECASE) if extra else _BOILERPLATE

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        lines = stats.lines
        if not lines:
            return QualitySignal(self.name, 0.0, True)
        if "lorem ipsum" in stats.text.lower():
            return QualitySignal(self.name, 1.0, False, detail={"lorem_ipsum": 1.0})
        # Only short lines count: long prose that mentions "subscribe" is not boilerplate.
        flagged = sum(1 for line in lines if len(line) < 120 and self._pattern.search(line))
        fraction = flagged / len(lines)
        return QualitySignal(self.name, fraction, fraction <= cfg.max_line_fraction)


class TerminalPunctuationSignal(Signal[TerminalPunctuationSignalConfig]):
    name = "terminal_punctuation"
    applies_to = frozenset({ContentKind.TEXT})

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        candidates = [line for line in stats.lines if len(line.split()) >= cfg.min_line_words]
        if not candidates:
            return QualitySignal(self.name, 0.0, False)
        fraction = sum(1 for line in candidates if line.endswith(_TERMINAL)) / len(candidates)
        return QualitySignal(self.name, fraction, fraction >= cfg.min_fraction)


class StopwordSignal(Signal[StopwordSignalConfig]):
    name = "stopwords"
    applies_to = TEXT_KINDS

    @override
    def applicable(self, document: Document) -> bool:
        cfg = self.config
        return super().applicable(document) and document.language in cfg.languages

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        present = len(_GOPHER_STOPWORDS.intersection(stats.words))
        return QualitySignal(self.name, float(present), present >= cfg.min_stopwords)


class LanguageConfidenceSignal(Signal[LanguageConfidenceSignalConfig]):
    name = "language_confidence"
    applies_to = TEXT_KINDS

    @override
    def applicable(self, document: Document) -> bool:
        return super().applicable(document) and document.metadata.language_confidence is not None

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        confidence = document.metadata.language_confidence or 0.0
        return QualitySignal(self.name, confidence, confidence >= cfg.min_confidence)


class SentenceLengthSignal(Signal[SentenceLengthSignalConfig]):
    name = "sentence_length"
    applies_to = frozenset({ContentKind.TEXT})

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        sentences = [s for s in _SENTENCE_SPLIT.split(stats.text) if s.strip()]
        if not sentences:
            return QualitySignal(self.name, 0.0, False)
        mean = len(stats.words) / len(sentences)
        return QualitySignal(self.name, mean, cfg.min_mean_words <= mean <= cfg.max_mean_words)


class CodeLineLengthSignal(Signal[CodeLineLengthSignalConfig]):
    name = "code_line_length"
    applies_to = frozenset({ContentKind.CODE})

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        lengths = [len(line) for line in stats.raw_lines]
        longest = max(lengths)
        mean = sum(lengths) / len(lengths)
        passed = longest <= cfg.max_line_length and mean <= cfg.max_mean_line_length
        return QualitySignal(self.name, mean, passed, detail={"max_line_length": float(longest)})


class AlnumRatioSignal(Signal[AlnumRatioSignalConfig]):
    name = "alnum_ratio"
    applies_to = frozenset({ContentKind.CODE})

    @override
    def measure(self, stats: TextStats, document: Document) -> QualitySignal:
        cfg = self.config
        text = stats.text
        ratio = sum(1 for c in text if c.isalnum()) / len(text) if text else 0.0
        return QualitySignal(self.name, ratio, ratio >= cfg.min_ratio)


_SIGNAL_TYPES: Final[dict[str, type[Signal[Any]]]] = {
    "length": LengthSignal,
    "mean_word_length": MeanWordLengthSignal,
    "alpha_words": AlphaWordsSignal,
    "symbol_ratio": SymbolRatioSignal,
    "line_repetition": LineRepetitionSignal,
    "ngram_repetition": NgramRepetitionSignal,
    "char_entropy": CharEntropySignal,
    "url_ratio": UrlRatioSignal,
    "boilerplate": BoilerplateSignal,
    "terminal_punctuation": TerminalPunctuationSignal,
    "stopwords": StopwordSignal,
    "language_confidence": LanguageConfidenceSignal,
    "sentence_length": SentenceLengthSignal,
    "code_line_length": CodeLineLengthSignal,
    "alnum_ratio": AlnumRatioSignal,
}


def build_signals(config: QualitySignalsConfig) -> list[Signal[Any]]:
    """Instantiate the enabled signals in a fixed, documented order."""
    signals: list[Signal[Any]] = []
    for name, signal_type in _SIGNAL_TYPES.items():
        signal_config: SignalConfig = getattr(config, name)
        if signal_config.enabled:
            signals.append(signal_type(signal_config))
    return signals
