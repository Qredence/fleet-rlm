"""Bounded, content-free operator preflight for provider-backed campaigns."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path


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
    cleanup. Unknown actual spend on a trial with confirmed cleanup charges
    the entire reservation and continues; it never releases budget based on
    an assumed zero. Unconfirmed cleanup still halts admission, because a
    leaked provider resource can bill outside the cap's visibility.
    """

    def __init__(
        self,
        policy: CampaignPreflight,
        *,
        started_at: float,
        cleanup_reserve_seconds: int = 900,
        initial_spent_usd: float = 0.0,
    ) -> None:
        policy.validate()
        if not math.isfinite(started_at):
            raise ValueError("campaign start must be finite")
        if type(cleanup_reserve_seconds) is not int or not 0 < cleanup_reserve_seconds < policy.max_elapsed_seconds:
            raise ValueError("cleanup reserve must fit within the campaign duration")
        self.policy = policy
        self.deadline = started_at + policy.max_elapsed_seconds
        self.admission_deadline = self.deadline - cleanup_reserve_seconds
        self._spent = self._amount(initial_spent_usd)
        if self._spent > self._amount(policy.total_spend_cap):
            raise ValueError("initial campaign spend exceeds the declared cap")
        self._reserved: Decimal | None = None
        self._admissions = 0
        self._halted = False
        self._halt_reason: str | None = None

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
        reserved = self._reserved
        if actual_usd is None or not cleanup_confirmed:
            # The reservation is the only defensible spend value when the
            # provider result or lifecycle proof is incomplete.  Charge it
            # before halting so the receipt and retention accounting cannot
            # accidentally release an owned bound.
            self._spent += reserved
            self._reserved = None
            self._halted = True
            self._halt_reason = "unconfirmed_cleanup" if not cleanup_confirmed else "unknown_spend"
            raise CampaignAdmissionError("trial spend or cleanup evidence is unavailable")
        actual = self._amount(actual_usd)
        if actual > reserved:
            self._spent += actual
            self._reserved = None
            self._halted = True
            self._halt_reason = "cost_bound_breach"
            raise CampaignAdmissionError("trial exceeded its asserted cost bound")
        self._spent += actual
        self._reserved = None

    def settle_unknown(self, *, cleanup_confirmed: bool) -> None:
        """Charge one full reservation for unknown actual spend and continue.

        Ordinary trial failures (model errors, parse failures) carry no
        usage telemetry by design; the campaign accounts them at the
        worst-case reservation instead of halting, so success-rate evidence
        can accumulate. The cap guarantee is preserved because every
        reservation was admitted against it. Unconfirmed cleanup still
        halts: leaked provider resources bill outside this ledger.
        """
        if self._reserved is None:
            raise CampaignAdmissionError("no trial reservation exists")
        reserved = self._reserved
        if not cleanup_confirmed:
            self._spent += reserved
            self._reserved = None
            self._halted = True
            self._halt_reason = "unconfirmed_cleanup"
            raise CampaignAdmissionError("trial spend or cleanup evidence is unavailable")
        self._spent += reserved
        self._reserved = None

    def receipt(self) -> dict[str, object]:
        return {
            "admissions": self._admissions,
            "observed_spend_usd": str(self._spent),
            "reserved_spend_usd": str(self._reserved) if self._reserved is not None else None,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        }


def write_receipt_once(path: Path, payload: Mapping[str, object], *, max_bytes: int | None = None) -> str:
    """Persist pre-sanitized JSON without replacing prior evidence.

    Serialization and optional byte bounds are checked before creating a file.
    Callers retain responsibility for schema validation and content projection.
    A failed write removes only the file exclusively created by this attempt.
    """
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ValueError("receipt exceeds its size bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    wrapped = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            wrapped = True
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if not wrapped:
            with suppress(OSError):
                os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CampaignAdmissionError",
    "CampaignBudget",
    "CampaignPreflight",
    "CampaignPreflightError",
    "observed_spend",
    "write_receipt_once",
]


def observed_spend(value: object) -> tuple[float, bool]:
    """Aggregate nested provider costs without double-counting totals.

    Fleet's canonical ``RLMUsage`` stores per-model observations under
    ``observed_lm_usage``. At each usage-entry mapping, a reported ``cost``
    takes precedence over ``input_cost``/``output_cost``; child mappings are
    not visited after a cost-bearing entry. The boolean is false when no
    complete finite observation exists, allowing a live campaign to fail
    closed instead of silently treating an unknown spend as zero.
    """
    if not isinstance(value, Mapping):
        return 0.0, False
    observed = value.get("observed_lm_usage")
    if not isinstance(observed, (Mapping, list, tuple)):
        return 0.0, False
    total = 0.0
    entries = 0
    complete = True

    def number(item: object) -> float | None:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numeric = float(item)
        return numeric if math.isfinite(numeric) and numeric >= 0 else None

    def visit(item: object) -> None:
        nonlocal total, entries, complete
        if isinstance(item, Mapping):
            has_cost = "cost" in item
            cost_value = item.get("cost")
            input_cost = item.get("input_cost")
            output_cost = item.get("output_cost")
            is_entry = any(
                key in item for key in ("cost", "input_cost", "output_cost", "prompt_tokens", "input_tokens")
            )
            if is_entry:
                entries += 1
                if has_cost:
                    # An explicitly reported total is authoritative, but a
                    # malformed total cannot be silently replaced with zero
                    # or a partial component sum.
                    cost = number(cost_value)
                else:
                    input_total = number(input_cost)
                    output_total = number(output_cost)
                    # Both components are required when no provider total is
                    # available. Treating a missing side as zero understates
                    # spend and defeats the campaign cap.
                    if input_total is None or output_total is None:
                        complete = False
                        return
                    cost = input_total + output_total
                if cost is None:
                    complete = False
                    return
                total += cost
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(observed)
    return total, bool(entries) and complete
