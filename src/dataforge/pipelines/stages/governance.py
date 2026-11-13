"""Governance stages: PII, secrets, and license policy.

Artifacts and logs carry counts and types only, never matched values.
"""

from __future__ import annotations

from collections import Counter

from typing_extensions import override

from dataforge.config.models import LicenseConfig, PIIConfig, SecretsConfig
from dataforge.domain.document import Document
from dataforge.licensing.policy import LicensePolicy
from dataforge.pipelines.stage import Emitter, Rejection, StageContext, StageResult, StreamingStage
from dataforge.privacy.pii import PIIScanner
from dataforge.privacy.secrets import build_secret_scanner
from dataforge.privacy.spans import Span, redact


def _record_spans(document: Document, spans: list[Span], emit: Emitter, artifact: str) -> dict[str, int]:
    counts = Counter(span.kind for span in spans)
    for kind, count in sorted(counts.items()):
        emit.count(f"detected|{kind}", count)
        emit.artifact(
            artifact,
            {
                "document_id": document.document_id,
                "source_id": document.source_id,
                "type": kind,
                "count": count,
            },
        )
    if counts:
        emit.count("documents_with_detections")
    return dict(sorted(counts.items()))


class PIIStage(StreamingStage):
    name = "pii"
    description = "Regex + checksum PII detection with detect/redact/drop policies"

    def __init__(self, config: PIIConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.scanner = PIIScanner(config.detectors)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        spans = self.scanner.scan(document.text)
        if not spans:
            return document
        document.metadata.pii_counts = _record_spans(document, spans, emit, "pii_detections")
        policy = self.cfg.policy
        if policy == "drop" and len(spans) >= self.cfg.drop_threshold:
            return Rejection("pii")
        if policy == "redact":
            document.replace_text(redact(document.text, spans), f"pii:redact[{len(spans)}]")
            emit.count("redactions", len(spans))
        return document


class SecretsStage(StreamingStage):
    name = "secrets"
    description = "Credential detection (known prefixes, assignments, entropy)"

    def __init__(self, config: SecretsConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.scanner = build_secret_scanner(config.detectors, config.min_entropy, config.min_length)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        spans = self.scanner.scan(document.text)
        if not spans:
            return document
        document.metadata.secret_counts = _record_spans(document, spans, emit, "secret_detections")
        if self.cfg.policy == "drop":
            return Rejection("secret")
        if self.cfg.policy == "redact":
            document.replace_text(redact(document.text, spans), f"secrets:redact[{len(spans)}]")
            emit.count("redactions", len(spans))
        return document


class LicenseStage(StreamingStage):
    name = "license"
    description = "License allowlist / unknown-license policy"

    def __init__(self, config: LicenseConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.policy = LicensePolicy(config)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        license_id = document.metadata.license
        decision = self.policy.evaluate(license_id, document.metadata.usage_policy)
        emit.count(f"license|{license_id}|documents")
        emit.count(f"license|{license_id}|bytes", document.num_bytes)
        emit.count(f"license|{license_id}|included", int(decision.allowed))
        if not decision.allowed:
            return Rejection(decision.reason)
        return document
