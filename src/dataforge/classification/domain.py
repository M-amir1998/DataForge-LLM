"""Rule-based domain classification.

Decision order: source hints (an operator's declaration beats any heuristic), code content,
URL rules, then keyword density per 100 words. This is a transparent baseline with known
blind spots (sarcasm, topic drift, short texts); a learned classifier can replace it behind
the same ``classify`` signature.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from dataforge.config.models import ClassifyConfig
from dataforge.domain.document import ContentKind

# fmt: off
DOMAINS: Final = (
    "general", "science", "technology", "code", "mathematics", "legal", "news", "books", "forums",
)
# fmt: on

_KEYWORDS: Final[dict[str, frozenset[str]]] = {
    "science": frozenset(
        "researchers research study experiment experiments hypothesis laboratory samples sample "
        "species measured measurements data analysis observed observations climate protein cell "
        "cells genome enzyme molecules physics chemistry biology ecological ecologists soil "
        "microbiome atmospheres replication reviewers survey isotope spectral".split()
    ),
    "technology": frozenset(
        "software hardware server servers cloud network database api latency deployment release "
        "engineers cluster clusters cache caching scheduler compiler firmware streaming workloads "
        "benchmarks operators configuration upgrade upgraded interface maintainers builds "
        "open-source documentation storage engine".split()
    ),
    "mathematics": frozenset(
        "theorem proof lemma integer integers equation polynomial matrix matrices derivative "
        "integral prime primes probability vector vectors induction contradiction discriminant "
        "bound sequence converges divisible determinant injective graph vertex roots estimate "
        "exercises symmetry".split()
    ),
    "legal": frozenset(
        "court plaintiff defendant statute pursuant section clause liability contract agreement "
        "tribunal appellate counsel damages jurisdiction tenancy licensee tenant supplier "
        "injunctive precedent breach indemnify judge ruling terms amendment".split()
    ),
    "news": frozenset(
        "officials announced spokesperson minister mayor council councillors residents reporters "
        "government election protesters vote headlines plan funding households analysts "
        "opposition governor community".split()
    ),
    "books": frozenset(
        "said whispered replied window letter morning evening silence door fire village house "
        "remembered family lantern tide hills shutters midnight".split()
    ),
    "forums": frozenset(
        "reply thanks anyone tips tried edit update suggestions advance setup issue problem "
        "fixed community weekend appreciated honestly".split()
    ),
}

_URL_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("arxiv", "science"),
    ("research", "science"),
    ("news", "news"),
    ("law", "legal"),
    ("legal", "legal"),
    ("github", "code"),
    ("gitlab", "code"),
    ("stackoverflow", "forums"),
    ("forum", "forums"),
    ("reddit", "forums"),
    ("tech", "technology"),
    ("docs", "technology"),
    ("math", "mathematics"),
    ("wiki", "general"),
)
_WORD: Final = re.compile(r"[a-z][a-z-]+")
_MAX_WORDS: Final = 3000


@dataclass(frozen=True, slots=True)
class DomainPrediction:
    domain: str
    confidence: float
    method: str


class DomainClassifier:
    def __init__(self, config: ClassifyConfig) -> None:
        self.config = config
        self.keywords: dict[str, frozenset[str]] = dict(_KEYWORDS)
        for domain, extra in config.extra_keywords.items():
            self.keywords[domain] = self.keywords.get(domain, frozenset()) | frozenset(extra)
        index: dict[str, list[str]] = {}
        for domain, vocabulary in sorted(self.keywords.items()):
            for word in vocabulary:
                index.setdefault(word, []).append(domain)
        self._index = {word: tuple(domains) for word, domains in index.items()}

    def _from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        host = urlparse(url).netloc.lower()
        for marker, domain in _URL_RULES:
            if marker in host:
                return domain
        return None

    def scores(self, text: str) -> Mapping[str, float]:
        """Keyword hits per 100 words for each domain."""
        words = _WORD.findall(text.lower())[:_MAX_WORDS]
        if not words:
            return {}
        counts = dict.fromkeys(self.keywords, 0)
        index = self._index
        for word in words:
            for domain in index.get(word, ()):
                counts[domain] += 1
        scale = 100.0 / len(words)
        return {domain: count * scale for domain, count in counts.items()}

    def classify(
        self,
        text: str,
        *,
        kind: ContentKind,
        domain_hint: str | None = None,
        url: str | None = None,
    ) -> DomainPrediction:
        cfg = self.config
        if cfg.use_source_hints and domain_hint:
            return DomainPrediction(domain_hint, 1.0, "source_hint")
        if kind is ContentKind.CODE:
            return DomainPrediction("code", 1.0, "content_kind")
        scores = self.scores(text)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        url_domain = self._from_url(url) if cfg.use_url_rules else None
        if ranked and ranked[0][1] >= cfg.min_score:
            best, best_score = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            confidence = (best_score - second) / best_score
            if url_domain is not None and url_domain != best and confidence < 0.5:
                return DomainPrediction(url_domain, 0.6, "url_rule")
            return DomainPrediction(best, round(confidence, 4), "keywords")
        if url_domain is not None:
            return DomainPrediction(url_domain, 0.6, "url_rule")
        return DomainPrediction(cfg.fallback_domain, 0.0, "fallback")
