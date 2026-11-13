"""Deterministic representative selection for duplicate groups.

* ``keep_first``           - earliest document in canonical ingestion order (default for
  exact duplicates: identical text, so only provenance differs).
* ``keep_longest``         - most bytes, ties broken by ingestion order (default for near
  duplicates: keeps the most complete version).
* ``keep_highest_quality`` - highest quality score, then most bytes, then ingestion order.
  Requires the quality stage to run earlier.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from dataforge.config.models import DuplicatePolicy


def representative_keys(
    policy: DuplicatePolicy,
    ordinals: npt.NDArray[np.int64],
    lengths: npt.NDArray[np.int64],
    quality: npt.NDArray[np.float32],
) -> list[npt.NDArray[np.generic]]:
    """Secondary sort keys (least significant first, as ``np.lexsort`` expects).

    Sorting a group by these keys puts its representative first.
    """
    if policy == "keep_first":
        return [ordinals]
    if policy == "keep_longest":
        return [ordinals, -lengths]
    filled = np.where(np.isnan(quality), -np.inf, quality).astype(np.float64)
    return [ordinals, -lengths, -filled]
