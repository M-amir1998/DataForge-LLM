"""Credential detection for code and text corpora.

Three complementary techniques:

* **known token formats** with fixed prefixes (AWS, GitHub, GitLab, Slack, Stripe, Google,
  Hugging Face, OpenAI-style keys, JWTs, PEM private keys);
* **credential assignments** (``password = "..."``, ``api_key: '...'``) with placeholder
  filtering;
* **Shannon entropy** gating, so ``token = "changeme"`` is ignored while a random 32-character
  value is flagged.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from dataforge.privacy.spans import Span, resolve_overlaps

_PREFIXED: Final[dict[str, re.Pattern[str]]] = {
    "private_key": re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]{16,16384}?"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    ),
    "aws_access_key_id": re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}(?![A-Z0-9])"),
    "github_token": re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{80,90})(?![A-Za-z0-9_])"
    ),
    "gitlab_token": re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    "slack_token": re.compile(r"(?<![A-Za-z0-9-])xox[abposr]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
    "stripe_secret_key": re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk)_live_[A-Za-z0-9]{20,}(?![A-Za-z0-9_])"),
    "google_api_key": re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
    "huggingface_token": re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z]{30,40}(?![A-Za-z0-9_])"),
    "openai_api_key": re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"),
    "jwt": re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
}
_AWS_SECRET: Final = re.compile(
    r"(?i)aws_?secret_?access_?key[\"']?\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"
)
_PASSWORD: Final = re.compile(
    r"(?i)(?<![\w.-])[\w.-]*(?:password|passwd|pwd|secret)[\w.-]*[\"']?\s*[:=]\s*"
    r"(?P<quote>[\"'])(?P<value>[^\"'\s]{6,256})(?P=quote)"
)
_HIGH_ENTROPY: Final = re.compile(
    r"(?i)(?<![\w.-])[\w.-]*(?:key|token|secret|credential|auth|bearer)[\w.-]*[\"']?\s*[:=]\s*"
    r"(?P<quote>[\"'])(?P<value>[A-Za-z0-9+/=_.-]{16,512})(?P=quote)"
)
# fmt: off
_PLACEHOLDERS: Final = (
    "${", "{{", "{", "<", "%s", "%(", "xxx", "***", "...", "changeme", "change_me", "password",
    "example", "your_", "your-", "dummy", "placeholder", "redacted", "secret", "todo", "none",
    "null", "env[", "os.environ", "getenv",
)
# fmt: on


def shannon_entropy(value: str) -> float:
    """Bits per character of the empirical character distribution."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum(count / length * math.log2(count / length) for count in counts.values())


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDERS) or len(set(value)) <= 3


@dataclass(frozen=True, slots=True)
class SecretScanner:
    detectors: tuple[str, ...]
    min_entropy: float = 3.5
    min_length: int = 12

    def _assignments(self, text: str) -> Iterator[Span]:
        enabled = set(self.detectors)
        if "aws_secret_access_key" in enabled:
            for match in _AWS_SECRET.finditer(text):
                if shannon_entropy(match.group("value")) >= self.min_entropy:
                    yield Span(match.start("value"), match.end("value"), "aws_secret_access_key", "secret")
        if "password_assignment" in enabled:
            for match in _PASSWORD.finditer(text):
                value = match.group("value")
                if not _is_placeholder(value) and shannon_entropy(value) >= min(self.min_entropy, 3.0):
                    yield Span(match.start("value"), match.end("value"), "password_assignment", "secret")
        if "high_entropy_assignment" in enabled:
            for match in _HIGH_ENTROPY.finditer(text):
                value = match.group("value")
                if (
                    len(value) >= self.min_length
                    and not _is_placeholder(value)
                    and shannon_entropy(value) >= self.min_entropy
                ):
                    yield Span(
                        match.start("value"),
                        match.end("value"),
                        "high_entropy_assignment",
                        "secret",
                    )

    def scan(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for kind in self.detectors:
            pattern = _PREFIXED.get(kind)
            if pattern is not None:
                spans.extend(Span(m.start(), m.end(), kind, "secret") for m in pattern.finditer(text))
        spans.extend(self._assignments(text))
        return resolve_overlaps(spans)


def build_secret_scanner(detectors: Sequence[str], min_entropy: float, min_length: int) -> SecretScanner:
    known = set(_PREFIXED) | {
        "aws_secret_access_key",
        "password_assignment",
        "high_entropy_assignment",
    }
    unknown = set(detectors) - known
    if unknown:
        raise ValueError(f"unknown secret detectors: {sorted(unknown)}")
    return SecretScanner(tuple(detectors), min_entropy, min_length)
