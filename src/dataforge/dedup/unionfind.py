"""Array-backed union-find with deterministic roots (the smallest index wins)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

IntArray = npt.NDArray[np.int64]


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent: IntArray = np.arange(size, dtype=np.int64)

    def find(self, item: int) -> int:
        parent = self.parent
        root = item
        while parent[root] != root:
            root = int(parent[root])
        while parent[item] != root:
            parent[item], item = root, int(parent[item])
        return root

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if a < b:
            self.parent[b] = a
        else:
            self.parent[a] = b

    def roots(self) -> IntArray:
        """Root of every element, fully compressed (vectorized pointer jumping)."""
        parent = self.parent.copy()
        while True:
            grand = parent[parent]
            if np.array_equal(grand, parent):
                return parent
            parent = grand
