from __future__ import annotations

import pytest

from dataforge.config.models import NormalizeConfig
from dataforge.domain.document import ContentKind
from dataforge.normalization.text import TextNormalizer

NORMALIZER = TextNormalizer(NormalizeConfig())


def normalize(text: str, kind: ContentKind = ContentKind.TEXT) -> str:
    return NORMALIZER.normalize(text, kind).text


def test_nfc_composes_combining_marks() -> None:
    assert normalize("cafe\N{COMBINING ACUTE ACCENT}") == "caf\N{LATIN SMALL LETTER E WITH ACUTE}"


def test_nfkc_is_opt_in() -> None:
    ligature = "\N{LATIN SMALL LIGATURE FI}le"
    assert normalize(ligature) == ligature
    assert TextNormalizer(NormalizeConfig(unicode_form="NFKC")).normalize(ligature).text == "file"


def test_control_characters_removed_but_tabs_and_newlines_kept() -> None:
    assert normalize("a\x00b\x07c\x1bd\x7fe\x85f\tg\nh") == "abcdef\tg\nh"


def test_zero_width_removed_but_joiners_kept() -> None:
    text = "x\N{ZERO WIDTH SPACE}y\N{WORD JOINER}z\N{ZERO WIDTH NON-JOINER}w\N{ZERO WIDTH JOINER}v"
    assert normalize(text) == "xyz\N{ZERO WIDTH NON-JOINER}w\N{ZERO WIDTH JOINER}v"


def test_line_endings_unified() -> None:
    assert normalize("a\r\nb\rc\N{LINE SEPARATOR}d\N{PARAGRAPH SEPARATOR}e") == "a\nb\nc\nd\ne"


def test_text_whitespace_rules() -> None:
    raw = "  Title\N{NO-BREAK SPACE}here   \n\n\n\n\nBody\N{EM SPACE}text\t \n\n"
    assert normalize(raw) == "Title here\n\n\nBody text"


def test_code_keeps_indentation_and_trailing_spaces() -> None:
    code = "\n\ndef f():\n    x = 1  \n\n\n\n    return x\N{NO-BREAK SPACE}\n"
    assert normalize(code, ContentKind.CODE) == "def f():\n    x = 1  \n\n\n\n    return x\N{NO-BREAK SPACE}"


def test_lone_surrogates_become_replacement_character() -> None:
    assert normalize("bad\ud800char") == "bad\N{REPLACEMENT CHARACTER}char"


def test_changes_are_reported_and_noop_is_clean() -> None:
    result = NORMALIZER.normalize("a\r\nb  ")
    assert result.changed
    assert set(result.changes) == {"line_endings", "trailing_ws"}
    assert not NORMALIZER.normalize("already clean").changed


@pytest.mark.parametrize(
    "text",
    [
        "e\x00\N{COMBINING ACUTE ACCENT}",
        "\N{ZERO WIDTH SPACE}\N{COMBINING ACUTE ACCENT}x",
        "a \n\N{COMBINING ACUTE ACCENT}",
        "\N{NO-BREAK SPACE}\N{COMBINING DIAERESIS}",
    ],
)
def test_idempotent_on_tricky_inputs(text: str) -> None:
    for kind in ContentKind:
        once = normalize(text, kind)
        assert normalize(once, kind) == once


def test_blank_line_cap_can_be_disabled() -> None:
    normalizer = TextNormalizer(NormalizeConfig(max_consecutive_blank_lines=None))
    assert normalizer.normalize("a\n\n\n\n\nb").text == "a\n\n\n\n\nb"
