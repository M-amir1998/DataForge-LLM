"""License and usage-policy gating.

Decisions are configuration, not legal conclusions: the allowlist encodes what the dataset
owner has decided to accept. Unknown licenses are rejected by default.
"""

from __future__ import annotations

from dataclasses import dataclass

from dataforge.config.models import LicenseConfig
from dataforge.domain.document import UNKNOWN_LICENSE
from dataforge.errors import UnsafeLicense
from dataforge.licensing.spdx import normalize_license


@dataclass(frozen=True, slots=True)
class LicenseDecision:
    allowed: bool
    reason: str


class LicensePolicy:
    def __init__(self, config: LicenseConfig) -> None:
        self.config = config
        self.allowed = frozenset(normalize_license(value) for value in config.allowed)
        self.denied = frozenset(normalize_license(value) for value in config.denied)
        self.usage = frozenset(config.allowed_usage_policies) if config.allowed_usage_policies is not None else None

    def evaluate(self, license_id: str, usage_policy: str | None = None) -> LicenseDecision:
        """Decide whether a document with ``license_id`` may enter the dataset.

        Raises:
            UnsafeLicense: when the matching policy is configured as ``fail``.
        """
        cfg = self.config
        if license_id == UNKNOWN_LICENSE:
            if cfg.unknown_policy == "fail":
                raise UnsafeLicense("document with UNKNOWN license and unknown_policy=fail")
            if cfg.unknown_policy == "reject":
                return LicenseDecision(False, "unknown_license")
        elif license_id in self.denied or license_id not in self.allowed:
            if cfg.disallowed_policy == "fail":
                raise UnsafeLicense(f"license '{license_id}' is not allowed")
            reason = "denied_license" if license_id in self.denied else "license_not_allowed"
            return LicenseDecision(False, reason)
        if self.usage is not None and (usage_policy or "unspecified") not in self.usage:
            return LicenseDecision(False, "usage_policy_not_allowed")
        return LicenseDecision(True, "allowed")

    def is_included(self, license_id: str) -> bool:
        """Non-raising view used by reports."""
        if license_id == UNKNOWN_LICENSE:
            return self.config.unknown_policy == "allow"
        return license_id in self.allowed and license_id not in self.denied
