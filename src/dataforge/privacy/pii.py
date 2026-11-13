"""Lightweight PII detection with structural validation.

Regex detection is a recall-oriented first line of defence, not a compliance guarantee: it
misses PII without a fixed shape (names, addresses, free-text health data) and can flag
look-alikes. Where a checksum or structure exists (Luhn for payment cards, ISO 13616 mod-97
for IBANs, ``ipaddress`` parsing for IPs, SSA area rules for SSNs) it is enforced to keep
false positives down.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from dataforge.privacy.spans import Span, resolve_overlaps

_EMAIL: Final = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}(?![\w-])"
)
_PHONE_NANP: Final = re.compile(r"(?<![\w+])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?![\w-])")
_PHONE_INTL: Final = re.compile(r"(?<![\w+])\+\d{1,3}(?:[ .-]\(?\d{1,4}\)?){2,5}(?![\w-])")
_IPV4: Final = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6: Final = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.])")
_VERSION_CONTEXT: Final = re.compile(r"(?:\bv|version\s*|release\s*)$", re.IGNORECASE)
_CARD: Final = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_SSN: Final = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")
_IBAN: Final = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_CARD_PREFIXES: Final = re.compile(r"^(?:4|5[1-5]|2[2-7]|3[47]|3[068]|35|6011|65|62)")


def luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = ord(char) - 48
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban_valid(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _find_email(text: str) -> Iterator[tuple[int, int]]:
    for match in _EMAIL.finditer(text):
        yield match.span()


def _find_phone(text: str) -> Iterator[tuple[int, int]]:
    for pattern in (_PHONE_NANP, _PHONE_INTL):
        for match in pattern.finditer(text):
            if 7 <= len(_digits(match.group())) <= 15:
                yield match.span()


def _find_ip(text: str) -> Iterator[tuple[int, int]]:
    for match in _IPV4.finditer(text):
        if _VERSION_CONTEXT.search(text[max(0, match.start() - 10) : match.start()]):
            continue
        try:
            address = ipaddress.IPv4Address(match.group())
        except ValueError:
            continue
        if not (address.is_unspecified or address.is_loopback or str(address) == "255.255.255.255"):
            yield match.span()
    for match in _IPV6.finditer(text):
        value = match.group()
        if value.count(":") < 2 or not any(c.isalnum() for c in value):
            continue
        try:
            address6 = ipaddress.IPv6Address(value)
        except ValueError:
            continue
        if not (address6.is_unspecified or address6.is_loopback):
            yield match.span()


def _find_card(text: str) -> Iterator[tuple[int, int]]:
    for match in _CARD.finditer(text):
        digits = _digits(match.group())
        if 13 <= len(digits) <= 19 and _CARD_PREFIXES.match(digits) and luhn_valid(digits):
            yield match.span()


def _find_ssn(text: str) -> Iterator[tuple[int, int]]:
    for match in _SSN.finditer(text):
        yield match.span()


def _find_iban(text: str) -> Iterator[tuple[int, int]]:
    for match in _IBAN.finditer(text):
        if iban_valid(match.group()):
            yield match.span()


@dataclass(frozen=True, slots=True)
class Detector:
    kind: str
    find: Callable[[str], Iterator[tuple[int, int]]]


PII_DETECTORS: Final[dict[str, Detector]] = {
    "email": Detector("email", _find_email),
    "phone": Detector("phone", _find_phone),
    "ip_address": Detector("ip_address", _find_ip),
    "credit_card": Detector("credit_card", _find_card),
    "us_ssn": Detector("us_ssn", _find_ssn),
    "iban": Detector("iban", _find_iban),
}


class PIIScanner:
    """Runs the configured detectors and returns non-overlapping spans."""

    def __init__(self, detectors: Sequence[str] = tuple(PII_DETECTORS)) -> None:
        unknown = set(detectors) - set(PII_DETECTORS)
        if unknown:
            raise ValueError(f"unknown PII detectors: {sorted(unknown)}")
        self.detectors = [PII_DETECTORS[name] for name in detectors]

    def scan(self, text: str) -> list[Span]:
        spans = [
            Span(start, end, detector.kind, "pii") for detector in self.detectors for start, end in detector.find(text)
        ]
        return resolve_overlaps(spans)
