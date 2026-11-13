"""PII and secret detection.

Secret-shaped test values are assembled from fragments at runtime so that no contiguous
credential-like literal exists in the repository (keeps secret scanners and push
protection quiet without allowlists).
"""

from __future__ import annotations

import pytest

from dataforge.config.models import ALL_SECRET_TYPES
from dataforge.privacy.pii import PIIScanner, iban_valid, luhn_valid
from dataforge.privacy.secrets import build_secret_scanner, shannon_entropy
from dataforge.privacy.spans import Span, redact, resolve_overlaps

PII = PIIScanner()
SECRETS = build_secret_scanner(ALL_SECRET_TYPES, min_entropy=3.5, min_length=12)
ALNUM = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5bC7dE9fG2hJ4"


def kinds(text: str) -> list[str]:
    return [span.kind for span in PII.scan(text)]


def secret_kinds(text: str) -> list[str]:
    return [span.kind for span in SECRETS.scan(text)]


# --------------------------------------------------------------------------- PII


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Write to jane.doe@example.com today.", ["email"]),
        ("Call +1 202-555-0143 or (312) 555-0199.", ["phone", "phone"]),
        ("International: +44 20 7946 0958.", ["phone"]),
        ("Server at 203.0.113.42 responded.", ["ip_address"]),
        ("IPv6 2001:db8::8a2e:370:7334 is reachable.", ["ip_address"]),
        ("Card 4111 1111 1111 1111 was charged.", ["credit_card"]),
        ("SSN 078-05-1120 on the form.", ["us_ssn"]),
        ("IBAN GB82 WEST 1234 5698 7654 32 please.", ["iban"]),
    ],
)
def test_pii_detected(text: str, expected: list[str]) -> None:
    assert kinds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Version 1.2.3.4 of the library was released.",
        "release 10.0.0.1 notes",
        "Loopback 127.0.0.1 and 0.0.0.0 are not personal.",
        "Order 1234 5678 9012 3456 is not a valid card.",
        "SSN-like 000-12-3456 and 666-12-3456 are invalid.",
        "Meet at 12:30:45 near the station.",
        "Email me at name at example dot com.",
        "Invalid IP 999.1.1.1 here.",
    ],
)
def test_pii_negatives(text: str) -> None:
    assert kinds(text) == []


def test_checksums() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE89 3704 0044 0532 0130 01")


def test_redaction_replaces_values_with_typed_placeholders() -> None:
    text = "Contact jane.doe@example.com or +1 202-555-0143."
    redacted = redact(text, PII.scan(text))
    assert redacted == "Contact <EMAIL> or <PHONE>."
    assert PII.scan(redacted) == []


def test_overlap_resolution_prefers_earlier_then_longer() -> None:
    spans = [Span(0, 5, "a", "pii"), Span(0, 9, "b", "pii"), Span(3, 7, "c", "pii"), Span(10, 12, "d", "pii")]
    assert [s.kind for s in resolve_overlaps(spans)] == ["b", "d"]


def test_spans_carry_no_values() -> None:
    span = PII.scan("x jane@example.com")[0]
    assert "jane" not in repr(span)


# --------------------------------------------------------------------------- secrets


def test_known_token_formats() -> None:
    samples = {
        "aws_access_key_id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "github_token": "gh" + "p_" + ALNUM[:36],
        "gitlab_token": "gl" + "pat-" + ALNUM[:20],
        "slack_token": "xo" + "xb-" + "123456789012-" + ALNUM[:24],
        "stripe_secret_key": "sk" + "_live_" + ALNUM[:24],
        "google_api_key": "AI" + "za" + ALNUM[:35],
        "huggingface_token": "hf" + "_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH",
        "openai_api_key": "sk" + "-proj-" + ALNUM[:40],
        "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N",
    }
    for kind, token in samples.items():
        assert secret_kinds(f"value = {token} end") == [kind], kind


def test_private_key_block() -> None:
    body = "\n".join(ALNUM[:48] for _ in range(3))
    block = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + body + "\n-----END " + "RSA PRIVATE KEY-----"
    assert secret_kinds(f"key = '''{block}'''") == ["private_key"]


def test_assignments_require_entropy_and_skip_placeholders() -> None:
    random_value = "Zq8" + "xT2#mW9pL4vR"
    assert secret_kinds(f'DB_PASSWORD = "{random_value}"') == ["password_assignment"]
    assert secret_kinds('password = "changeme123"') == []
    assert secret_kinds('password = "${DB_PASSWORD}"') == []
    assert secret_kinds('api_key = "<your-api-key-here>"') == []
    assert secret_kinds('token: "aaaaaaaaaaaaaaaaaaaaaaaa"') == []
    high_entropy = "Q2x" + "hc3NpY0VudHJvcHlWYWx1ZTEy"
    assert secret_kinds(f'auth_token: "{high_entropy}"') == ["high_entropy_assignment"]


def test_aws_secret_near_keyword() -> None:
    value = "wJalr" + "XUtnFEMI/K7MDENG/bPxRfiCY1x2Q4zA8kL"
    assert len(value) == 40  # AWS secret access keys are exactly 40 characters
    assert secret_kinds(f"aws_secret_access_key = {value}") == ["aws_secret_access_key"]
    assert secret_kinds(f"aws_secret_access_key = {value}X") == []


def test_redacting_secrets_uses_generic_placeholder() -> None:
    token = "gh" + "p_" + ALNUM[:36]
    text = f"export GITHUB_TOKEN={token}"
    assert redact(text, SECRETS.scan(text)) == "export GITHUB_TOKEN=<SECRET>"


def test_unknown_detector_rejected_and_entropy() -> None:
    with pytest.raises(ValueError, match="unknown secret detectors"):
        build_secret_scanner(["nope"], 3.5, 12)
    with pytest.raises(ValueError, match="unknown PII detectors"):
        PIIScanner(["nope"])
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abcd") == pytest.approx(2.0)
