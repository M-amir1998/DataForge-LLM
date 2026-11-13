"""Hashing primitives used for identity, deduplication, and integrity.

Two hash families are used deliberately:

* **xxh3-128** for high-volume content identity (document ids, content hashes,
  dedup keys): fast, well distributed, 128 bits make accidental collisions negligible.
* **SHA-256** for integrity of artifacts (source files, configs, shards, manifests):
  tamper-evident and universally verifiable with standard tools.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

import xxhash

_SEP: Final = b"\x1f"
_CHUNK: Final = 1 << 20
_UINT64_RANGE: Final = float(1 << 64)


def _utf8(text: str) -> bytes:
    # ``surrogatepass`` keeps hashing total even for lone surrogates that JSON can carry.
    return text.encode("utf-8", "surrogatepass")


def content_hash(text: str) -> str:
    """Hex xxh3-128 digest of ``text`` (32 lowercase hex characters)."""
    return xxhash.xxh3_128_hexdigest(_utf8(text))


def stable_document_id(source_id: str, record_key: str, raw_text: str) -> str:
    """Deterministic document id from source identity, record key, and raw content.

    The same input record always yields the same id across reruns and machines; any
    change to the record's content yields a new id (a new document version).
    """
    hasher = xxhash.xxh3_128()
    hasher.update(_utf8(source_id))
    hasher.update(_SEP)
    hasher.update(_utf8(record_key))
    hasher.update(_SEP)
    hasher.update(_utf8(raw_text))
    return hasher.hexdigest()


def hash_parts(*parts: str) -> str:
    """xxh3-128 hex digest of separator-joined string parts."""
    hasher = xxhash.xxh3_128()
    for index, part in enumerate(parts):
        if index:
            hasher.update(_SEP)
        hasher.update(_utf8(part))
    return hasher.hexdigest()


def unit_interval(key: str, salt: str) -> float:
    """Map ``key`` to a deterministic pseudo-uniform float in ``[0, 1)``.

    Used for stable sampling and split assignment: the value depends only on the key and
    the salt, never on iteration order or process state.
    """
    digest = xxhash.xxh3_64_intdigest(_utf8(salt) + _SEP + _utf8(key))
    return digest / _UINT64_RANGE


def hex_to_uint64_pair(hex_digest: str) -> tuple[int, int]:
    """Split a 32-hex-character digest into (high, low) unsigned 64-bit integers."""
    if len(hex_digest) != 32:
        raise ValueError(f"expected a 128-bit hex digest, got {len(hex_digest)} characters")
    return int(hex_digest[:16], 16), int(hex_digest[16:], 16)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file (bounded memory)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    """Canonical JSON encoding: sorted keys, no insignificant whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``value``."""
    return sha256_bytes(canonical_json(value))


def combine_fingerprints(parts: Iterable[str]) -> str:
    """Order-sensitive SHA-256 over a sequence of fingerprints."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("ascii"))
        digest.update(_SEP)
    return digest.hexdigest()
