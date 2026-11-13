"""Code-vs-prose detection from metadata and syntax characteristics.

Signals, in priority order:

1. source metadata (a ``code`` source, or a code MIME type from the file extension);
2. line-level syntax heuristics: code-like line endings, keywords, operator density,
   indentation, versus sentence-like lines.

The output drives format-aware normalization, quality scoring, and mixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from dataforge.domain.document import ContentKind

_KEYWORDS: Final = re.compile(
    r"^\s*(?:def |class |import |from \S+ import |return\b|function\b|const |let |var |public |"
    r"private |protected |static |void |func |package |fn |pub |struct |impl |enum |interface |"
    r"#include|using |namespace |SELECT |INSERT |CREATE TABLE|if \(|for \(|while \(|} else|"
    r"elif |except|try:|async |await |@\w+)"
)
_CODE_ENDINGS: Final = (";", "{", "}", "):", ")", "];", "},", "=>", "->")
_OPERATORS: Final = re.compile(r"[{}\[\]();=<>+\-*/&|!%^~]")
_SENTENCE: Final = re.compile(r"^[\"'(]?[A-Z][^{};=<>]*[.!?][\"')]?$")
_FENCE: Final = re.compile(r"^\s*```")
_URL: Final = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+", re.IGNORECASE)
_MAX_LINES: Final = 400


@dataclass(frozen=True, slots=True)
class CodeAssessment:
    kind: ContentKind
    code_line_fraction: float


def is_code_line(line: str) -> bool:
    # URLs are full of "/", ":" and "." but are not code; judge the rest of the line.
    stripped = _URL.sub(" ", line).strip()
    if not stripped:
        return False
    if _SENTENCE.match(stripped) and len(stripped.split()) >= 5:
        return False
    if _KEYWORDS.match(line):
        return True
    if stripped.endswith(_CODE_ENDINGS):
        return True
    if stripped.startswith(("//", "/*", "* ", "#!", "<?")):
        return True
    operators = len(_OPERATORS.findall(stripped))
    return operators >= 3 and operators / len(stripped) > 0.12


def assess(text: str, *, threshold: float = 0.5) -> CodeAssessment:
    """Classify text as code, mixed (prose with code blocks), or text."""
    lines = [line for line in text.splitlines()[:_MAX_LINES] if line.strip()]
    if not lines:
        return CodeAssessment(ContentKind.TEXT, 0.0)
    in_fence = False
    code_lines = 0
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            code_lines += 1
            continue
        if in_fence or is_code_line(line):
            code_lines += 1
    fraction = code_lines / len(lines)
    if fraction >= threshold:
        return CodeAssessment(ContentKind.CODE, fraction)
    if fraction >= min(0.15, threshold / 2):
        return CodeAssessment(ContentKind.MIXED, fraction)
    return CodeAssessment(ContentKind.TEXT, fraction)
