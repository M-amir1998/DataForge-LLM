"""License identifier normalization.

Only well-known spellings are mapped to SPDX identifiers. Anything unrecognized is kept
verbatim (never guessed), and missing values become ``UNKNOWN``. This module encodes
string normalization, not legal advice.
"""

from __future__ import annotations

import re
from typing import Final

from dataforge.domain.document import UNKNOWN_LICENSE

# fmt: off
_SPDX_IDS: Final = (
    "0BSD", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-2.0", "Artistic-2.0", "BSD-2-Clause",
    "BSD-3-Clause", "BSL-1.0", "CC-BY-3.0", "CC-BY-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    "CC-BY-ND-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0", "CC0-1.0", "EPL-2.0", "GFDL-1.3-only",
    "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "ISC",
    "LGPL-2.1-only", "LGPL-3.0-only", "MIT", "MIT-0", "MPL-2.0", "ODC-By-1.0", "ODbL-1.0",
    "PDDL-1.0", "Unlicense", "Zlib", "LicenseRef-PublicDomain",
)
# fmt: on
_BY_LOWER: Final = {identifier.lower(): identifier for identifier in _SPDX_IDS}
_ALIASES: Final = {
    "mit license": "MIT",
    "the mit license": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "cc0": "CC0-1.0",
    "cc by 4.0": "CC-BY-4.0",
    "cc-by 4.0": "CC-BY-4.0",
    "cc by-sa 4.0": "CC-BY-SA-4.0",
    "cc by-sa 3.0": "CC-BY-SA-3.0",
    "gplv3": "GPL-3.0-only",
    "gpl-3.0": "GPL-3.0-only",
    "gplv2": "GPL-2.0-only",
    "gpl-2.0": "GPL-2.0-only",
    "lgpl-2.1": "LGPL-2.1-only",
    "agpl-3.0": "AGPL-3.0-only",
    "public domain": "LicenseRef-PublicDomain",
    "public-domain": "LicenseRef-PublicDomain",
}
_UNKNOWN_MARKERS: Final = frozenset({"", "unknown", "none", "n/a", "na", "null", "unspecified", "noassertion"})
_SPACES: Final = re.compile(r"\s+")


def normalize_license(value: str | None) -> str:
    """Map a license string to its SPDX identifier when unambiguous; never infer."""
    if value is None:
        return UNKNOWN_LICENSE
    cleaned = _SPACES.sub(" ", value).strip()
    lowered = cleaned.lower()
    if lowered in _UNKNOWN_MARKERS:
        return UNKNOWN_LICENSE
    if lowered in _BY_LOWER:
        return _BY_LOWER[lowered]
    return _ALIASES.get(lowered, cleaned)


def is_known_spdx(identifier: str) -> bool:
    return identifier.lower() in _BY_LOWER
