"""Token-count estimation without requiring model weights or a GPU.

* ``chars`` - ``ceil(characters / chars_per_token)``; ~4 characters per token is the usual
  rule of thumb for English BPE vocabularies.
* ``words`` - ``ceil(words * tokens_per_word)``; more stable across whitespace-heavy code.
* ``hf``    - exact counts from a local ``tokenizer.json`` via the ``tokenizers`` library
  (``tokenizers`` extra; a few MB, CPU only).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Protocol

from dataforge.config.models import TokenizerConfig
from dataforge.errors import ConfigError, OptionalDependencyMissing

_WORD: Final = re.compile(r"\S+")


class TokenEstimator(Protocol):
    name: str

    def estimate(self, text: str) -> int: ...

    def estimate_batch(self, texts: Sequence[str]) -> list[int]: ...


class CharTokenEstimator:
    def __init__(self, chars_per_token: float = 4.0) -> None:
        self.chars_per_token = chars_per_token
        self.name = f"chars/{chars_per_token:g}"

    def estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)

    def estimate_batch(self, texts: Sequence[str]) -> list[int]:
        return [self.estimate(text) for text in texts]


class WordTokenEstimator:
    def __init__(self, tokens_per_word: float = 1.3) -> None:
        self.tokens_per_word = tokens_per_word
        self.name = f"words*{tokens_per_word:g}"

    def estimate(self, text: str) -> int:
        return math.ceil(len(_WORD.findall(text)) * self.tokens_per_word)

    def estimate_batch(self, texts: Sequence[str]) -> list[int]:
        return [self.estimate(text) for text in texts]


class HFTokenizerAdapter:
    """Exact token counts from a Hugging Face ``tokenizer.json`` file."""

    def __init__(self, tokenizer_path: Path) -> None:
        try:
            from tokenizers import Tokenizer  # noqa: PLC0415 - optional extra
        except ImportError as exc:
            raise OptionalDependencyMissing("Hugging Face tokenizer counts", "tokenizers") from exc
        if not tokenizer_path.is_file():
            raise ConfigError(f"tokenizer file not found: {tokenizer_path}")
        self._tokenizer: Any = Tokenizer.from_file(str(tokenizer_path))
        self.name = f"hf:{tokenizer_path.name}"

    def estimate(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def estimate_batch(self, texts: Sequence[str]) -> list[int]:
        encodings = self._tokenizer.encode_batch(list(texts), add_special_tokens=False)
        return [len(encoding.ids) for encoding in encodings]


def build_estimator(config: TokenizerConfig, base_dir: Path) -> TokenEstimator:
    if config.kind == "words":
        return WordTokenEstimator(config.tokens_per_word)
    if config.kind == "hf":
        if config.tokenizer_path is None:  # guaranteed by TokenizerConfig validation
            raise ConfigError("tokenizer kind 'hf' needs tokenizer_path")
        path = Path(config.tokenizer_path)
        return HFTokenizerAdapter(path if path.is_absolute() else base_dir / path)
    return CharTokenEstimator(config.chars_per_token)
