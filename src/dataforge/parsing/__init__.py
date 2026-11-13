"""Document parsing: decoding, format detection, and text extraction."""

from dataforge.parsing.base import ParsedText, Parser, ParserRegistry
from dataforge.parsing.encoding import DecodedText, decode_bytes
from dataforge.parsing.html import html_to_text

__all__ = [
    "DecodedText",
    "ParsedText",
    "Parser",
    "ParserRegistry",
    "decode_bytes",
    "html_to_text",
]
