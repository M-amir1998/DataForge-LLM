"""Byte decoding with BOM handling and conservative charset detection."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Final, Literal

import charset_normalizer

_BOMS: Final[tuple[tuple[bytes, str], ...]] = (
    # UTF-32 must be tested before UTF-16: the UTF-32-LE BOM starts with the UTF-16-LE BOM.
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: Below this fraction of undecodable bytes the input is treated as damaged UTF-8 rather
#: than a legacy encoding; re-interpreting mostly-valid UTF-8 as cp1252 creates mojibake.
_NEAR_UTF8_MAX_REPLACEMENT_RATIO: Final = 0.01


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    replaced: bool


def decode_bytes(data: bytes, encoding: str = "auto", errors: Literal["replace", "strict"] = "replace") -> DecodedText:
    """Decode ``data``.

    With ``encoding="auto"``: honour a BOM, then try strict UTF-8, then treat nearly-valid
    UTF-8 as UTF-8 with replacement characters, and only then ask charset-normalizer.

    Raises:
        UnicodeDecodeError: if ``errors="strict"`` and the bytes cannot be decoded losslessly.
    """
    if encoding != "auto":
        try:
            return DecodedText(data.decode(encoding, "strict"), encoding, False)
        except UnicodeDecodeError:
            if errors == "strict":
                raise
            return DecodedText(data.decode(encoding, "replace"), encoding, True)

    for bom, bom_encoding in _BOMS:
        if data.startswith(bom):
            try:
                return DecodedText(data.decode(bom_encoding, "strict"), bom_encoding, False)
            except UnicodeDecodeError:
                if errors == "strict":
                    raise
                return DecodedText(data.decode(bom_encoding, "replace"), bom_encoding, True)

    try:
        return DecodedText(data.decode("utf-8", "strict"), "utf-8", False)
    except UnicodeDecodeError as utf8_error:
        replaced = data.decode("utf-8", "replace")
        bad = replaced.count("\ufffd")
        if bad <= max(1, int(len(replaced) * _NEAR_UTF8_MAX_REPLACEMENT_RATIO)):
            if errors == "strict":
                raise
            return DecodedText(replaced, "utf-8", True)
        best = charset_normalizer.from_bytes(data).best()
        if best is not None and best.encoding:
            return DecodedText(str(best), best.encoding, False)
        if errors == "strict":
            raise utf8_error from None
        return DecodedText(replaced, "utf-8", True)
