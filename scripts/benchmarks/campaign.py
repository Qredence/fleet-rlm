"""Bounded, content-free operator preflight for provider-backed campaigns."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal


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


class CampaignAdmissionError(RuntimeError):
    """A campaign cannot admit more paid work within its declared limits."""


class CampaignBudget:
    """Serial trial reservations made before provider or sandbox admission.

    A reservation includes worst-case retries, tokens, sandbox lifetime, and
    cleanup. Unknown actual spend retains the entire reservation and stops
    further admissions; it never releases budget based on an assumed zero.
    """

    def __init__(self, policy: CampaignPreflight, *, started_at: float, cleanup_reserve_seconds: int = 900) -> None:
        policy.validate()
        if not math.isfinite(started_at):
            raise ValueError("campaign start must be finite")
        if type(cleanup_reserve_seconds) is not int or not 0 < cleanup_reserve_seconds < policy.max_elapsed_seconds:
            raise ValueError("cleanup reserve must fit within the campaign duration")
        self.policy = policy
        self.deadline = started_at + policy.max_elapsed_seconds
        self.admission_deadline = self.deadline - cleanup_reserve_seconds
        self._spent = Decimal(0)
        self._reserved: Decimal | None = None
        self._admissions = 0
        self._halted = False

    @staticmethod
    def _amount(value: float) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("campaign cost must be finite and nonnegative")
        return Decimal(str(value))

    def reserve(self, *, upper_bound_usd: float, now: float, max_trial_seconds: float) -> int:
        bound = self._amount(upper_bound_usd)
        if not math.isfinite(now) or not math.isfinite(max_trial_seconds) or max_trial_seconds <= 0:
            raise ValueError("trial time bounds must be finite and positive")
        if self._halted or self._reserved is not None:
            raise CampaignAdmissionError("campaign admission is halted or a trial remains owned")
        if self._admissions >= self.policy.max_admissions:
            raise CampaignAdmissionError("campaign admission limit reached")
        if now + max_trial_seconds > self.admission_deadline:
            raise CampaignAdmissionError("campaign execution and cleanup time reserve exhausted")
        if bound <= 0 or self._spent + bound > self._amount(self.policy.total_spend_cap):
            raise CampaignAdmissionError("campaign spend reservation exceeds remaining budget")
        self._reserved = bound
        self._admissions += 1
        return self._admissions

    def settle(self, *, actual_usd: float | None, cleanup_confirmed: bool) -> None:
        if self._reserved is None:
            raise CampaignAdmissionError("no trial reservation exists")
        if actual_usd is None or not cleanup_confirmed:
            self._halted = True
            raise CampaignAdmissionError("trial spend or cleanup evidence is unavailable")
        actual = self._amount(actual_usd)
        if actual > self._reserved:
            self._spent += actual
            self._reserved = None
            self._halted = True
            raise CampaignAdmissionError("trial exceeded its asserted cost bound")
        self._spent += actual
        self._reserved = None

    def receipt(self) -> dict[str, object]:
        return {
            "admissions": self._admissions,
            "observed_spend_usd": str(self._spent),
            "reserved_spend_usd": str(self._reserved) if self._reserved is not None else None,
            "halted": self._halted,
        }


__all__ = ["CampaignAdmissionError", "CampaignBudget", "CampaignPreflight", "CampaignPreflightError"]
