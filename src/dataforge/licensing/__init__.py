"""License normalization and policy filtering (not legal advice)."""

from dataforge.licensing.policy import LicenseDecision, LicensePolicy
from dataforge.licensing.spdx import is_known_spdx, normalize_license

__all__ = ["LicenseDecision", "LicensePolicy", "is_known_spdx", "normalize_license"]
