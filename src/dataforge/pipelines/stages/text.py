"""Text-level stages: normalization, language identification, paragraph deduplication."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from typing_extensions import override

from dataforge.classification.code import assess
from dataforge.classification.language import UNDETERMINED, build_detector
from dataforge.config.models import LanguageConfig, NormalizeConfig, ParagraphDedupConfig
from dataforge.dedup.paragraph import ParagraphDeduplicator
from dataforge.domain.document import ContentKind, Document
from dataforge.normalization.text import TextNormalizer
from dataforge.pipelines.stage import Emitter, Rejection, StageContext, StageResult, StreamingStage


class NormalizeStage(StreamingStage):
    name = "normalize"
    description = "Unicode/line-ending/whitespace normalization and code detection"

    def __init__(self, config: NormalizeConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.normalizer = TextNormalizer(config)

    def _kind(self, document: Document) -> ContentKind:
        source = self.context.source_config(document.source_id)
        hint = source.content_kind if source is not None else "auto"
        if hint != "auto" or document.metadata.content_kind is ContentKind.CODE:
            return document.metadata.content_kind
        if not self.cfg.detect_code:
            return ContentKind.TEXT
        return assess(document.text, threshold=self.cfg.code_threshold).kind

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        kind = self._kind(document)
        document.metadata.content_kind = kind
        emit.count(f"content_kind|{kind.value}")
        result = self.normalizer.normalize(document.text, kind)
        if result.changed:
            document.replace_text(result.text, "normalize[" + ",".join(result.changes) + "]")
            emit.count("changed")
            for change in result.changes:
                emit.count(f"change|{change}")
        if len(document.text) < self.cfg.min_chars:
            return Rejection("empty_after_normalization")
        return document


class LanguageStage(StreamingStage):
    name = "language"
    description = "Language identification with optional allowlist and confidence filter"

    def __init__(self, config: LanguageConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.detector = build_detector(config.detector, config.sample_chars)
        self.allowed = frozenset(config.allowed)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        cfg = self.cfg
        if cfg.skip_code and document.metadata.content_kind is ContentKind.CODE:
            emit.count("language|code")
            return document
        prediction = self.detector.detect(document.text)
        language = prediction.language
        if language == UNDETERMINED and document.language:
            language = document.language  # fall back to the source's declared language
        document.language = language
        document.metadata.language_confidence = prediction.confidence
        emit.count(f"language|{language}")
        if self.allowed and language not in self.allowed:
            return Rejection("language_not_allowed")
        if prediction.confidence < cfg.min_confidence:
            return Rejection("low_language_confidence")
        return document


class ParagraphDedupStage(StreamingStage):
    name = "paragraph_dedup"
    stateful: ClassVar[bool] = True
    description = "Corpus-level removal of repeated paragraphs (fixed-memory Bloom filter)"

    def __init__(self, config: ParagraphDedupConfig, context: StageContext) -> None:
        super().__init__(config, context)
        self.cfg = config
        self.dedup = ParagraphDeduplicator(config)
        self.kinds = frozenset(ContentKind(kind) for kind in config.apply_to)

    @override
    def process(self, document: Document, emit: Emitter) -> StageResult:
        if document.metadata.content_kind not in self.kinds:
            return document
        outcome = self.dedup.process(document.text)
        if outcome.removed:
            emit.count("paragraphs_removed", outcome.removed)
            emit.count("chars_removed", outcome.removed_chars)
            if not outcome.text:
                return Rejection("all_paragraphs_duplicate")
            document.replace_text(outcome.text, f"paragraph_dedup[-{outcome.removed}]")
        return document

    @override
    def save_state(self, path: Path) -> None:
        self.dedup.save(path)

    @override
    def load_state(self, path: Path) -> None:
        self.dedup.load(path)
