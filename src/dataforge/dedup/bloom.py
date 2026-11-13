"""Fixed-memory Bloom filter (numpy bit array, double hashing over xxh3-128)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import xxhash

_MASK64 = (1 << 64) - 1


class BloomFilter:
    """Set-membership sketch whose memory is fixed up front by capacity and error rate."""

    def __init__(self, capacity: int, error_rate: float) -> None:
        if capacity <= 0 or not 0.0 < error_rate < 1.0:
            raise ValueError("capacity must be positive and 0 < error_rate < 1")
        self.num_bits = max(8, math.ceil(-capacity * math.log(error_rate) / math.log(2) ** 2))
        self.num_hashes = max(1, round(self.num_bits / capacity * math.log(2)))
        self.bits = np.zeros((self.num_bits + 7) // 8, dtype=np.uint8)

    @property
    def nbytes(self) -> int:
        return int(self.bits.nbytes)

    def _positions(self, key: bytes) -> list[int]:
        digest = xxhash.xxh3_128_intdigest(key)
        h1, h2 = digest >> 64, (digest & _MASK64) | 1
        return [(h1 + i * h2) % self.num_bits for i in range(self.num_hashes)]

    def add(self, key: bytes) -> bool:
        """Insert ``key``; return ``True`` if it was (probably) already present."""
        present = True
        bits = self.bits
        for position in self._positions(key):
            byte, mask = position >> 3, 1 << (position & 7)
            if not bits[byte] & mask:
                present = False
                bits[byte] |= mask
        return present

    def __contains__(self, key: bytes) -> bool:
        bits = self.bits
        return all(bits[p >> 3] & (1 << (p & 7)) for p in self._positions(key))

    def save(self, path: Path) -> None:
        self.bits.tofile(path)

    def load(self, path: Path) -> None:
        data = np.fromfile(path, dtype=np.uint8)
        if data.shape != self.bits.shape:
            raise ValueError("bloom filter snapshot does not match the configured size")
        self.bits = data
