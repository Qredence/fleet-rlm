"""Bounded, content-free operator preflight for provider-backed campaigns."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass


class CampaignPreflightError(ValueError):
    """Raised when a live campaign does not have explicit safety limits."""


_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CampaignPreflight:
    """Operator-supplied limits and target reference for one live campaign.

    These values are deliberately labels and numeric bounds only. Credentials,
    URLs, prompts, fixtures, and raw provider responses never belong here or in
    the resulting receipt.
    """

    name: str
    target: str
    max_elapsed_seconds: int
    max_admissions: int
    max_sandbox_concurrency: int
    total_spend_cap: float

    def validate(self) -> None:
        if not _REFERENCE.fullmatch(self.name.strip()):
            raise CampaignPreflightError("campaign name must be a bounded operator reference")
        if not _REFERENCE.fullmatch(self.target.strip()):
            raise CampaignPreflightError("campaign target must be a bounded operator reference")
        if type(self.max_elapsed_seconds) is not int or self.max_elapsed_seconds <= 0:
            raise CampaignPreflightError("maximum campaign elapsed time must be positive")
        if type(self.max_admissions) is not int or self.max_admissions <= 0:
            raise CampaignPreflightError("maximum campaign admissions must be positive")
        if type(self.max_sandbox_concurrency) is not int or self.max_sandbox_concurrency <= 0:
            raise CampaignPreflightError("maximum sandbox concurrency must be positive")
        if (
            isinstance(self.total_spend_cap, bool)
            or not isinstance(self.total_spend_cap, (int, float))
            or not math.isfinite(float(self.total_spend_cap))
        ):
            raise CampaignPreflightError("total campaign spend cap must be finite")
        if self.total_spend_cap <= 0:
            raise CampaignPreflightError("total campaign spend cap must be positive")

    def as_dict(self) -> dict[str, object]:
        """Return the bounded receipt-safe representation."""
        self.validate()
        return asdict(self)


__all__ = ["CampaignPreflight", "CampaignPreflightError"]
