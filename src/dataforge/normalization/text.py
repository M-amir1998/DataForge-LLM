"""Conservative, idempotent text normalization.

Step order matters for idempotence: characters are removed *before* Unicode composition,
otherwise deleting a control character between a base letter and a combining mark would
let a second pass compose them differently.

1. lone surrogates -> U+FFFD (always; output must be valid UTF-8)
2. control characters (C0 except tab/newline/CR, DEL, C1) removed
3. zero-width space, word joiner, and stray BOMs removed (ZWJ/ZWNJ are kept: they carry
   meaning in Persian, Indic scripts, and emoji sequences)
4. CRLF / CR / U+2028 / U+2029 -> LF
5. Unicode normalization (NFC by default; NFKC is lossy and opt-in)
6. text only: exotic spaces -> ASCII space, trailing whitespace stripped, blank-line runs capped
7. trim the document (code keeps leading indentation)

Code documents only get steps 1-5 and 7, so indentation, alignment, and trailing
whitespace that may be significant survive untouched.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from dataforge.config.models import NormalizeConfig
from dataforge.domain.document import ContentKind

_SURROGATES: Final = re.compile("[\ud800-\udfff]")
_CONTROL: Final = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ZERO_WIDTH: Final = re.compile("[\u200b\u2060\ufeff]")
_LINE_BREAKS: Final = re.compile("\r\n|[\r\u2028\u2029]")
_EXOTIC_SPACES: Final = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f]")
_TRAILING_WS: Final = re.compile("[ \t]+$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    text: str
    changes: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


class TextNormalizer:
    """Applies :class:`NormalizeConfig` to document text; safe to run repeatedly."""

    def __init__(self, config: NormalizeConfig) -> None:
        self.config = config
        blank_limit = config.max_consecutive_blank_lines
        self._blank_runs = re.compile("\n{" + str(blank_limit + 2) + ",}") if blank_limit is not None else None
        self._blank_replacement = "\n" * (blank_limit + 1) if blank_limit is not None else ""

    def normalize(self, text: str, kind: ContentKind = ContentKind.TEXT) -> NormalizationResult:
        cfg = self.config
        changes: list[str] = []

        def apply(tag: str, new: str) -> None:
            nonlocal text
            if new != text:
                changes.append(tag)
                text = new

        apply("surrogates", _SURROGATES.sub("\ufffd", text))
        if cfg.strip_control_chars:
            apply("control_chars", _CONTROL.sub("", text))
        if cfg.remove_zero_width:
            apply("zero_width", _ZERO_WIDTH.sub("", text))
        if cfg.normalize_line_endings:
            apply("line_endings", _LINE_BREAKS.sub("\n", text))
        if cfg.unicode_form != "none":
            apply(cfg.unicode_form.lower(), unicodedata.normalize(cfg.unicode_form, text))

        prose = kind is not ContentKind.CODE
        if prose:
            if cfg.normalize_spaces:
                apply("spaces", _EXOTIC_SPACES.sub(" ", text))
            if cfg.strip_trailing_whitespace:
                apply("trailing_ws", _TRAILING_WS.sub("", text))
            if self._blank_runs is not None:
                apply("blank_lines", self._blank_runs.sub(self._blank_replacement, text))
        if cfg.trim_document:
            apply("trim", text.strip() if prose else text.strip("\n"))
        return NormalizationResult(text, tuple(changes))
