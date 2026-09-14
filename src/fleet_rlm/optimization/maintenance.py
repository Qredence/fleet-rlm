"""Maintenance-window admission fencing for Phase 6 release switches.

The controller owns ordering, but it does not pretend that an in-process flag
is a production fence.  A deployment composition supplies the adapter backed by
the real admission store, Run claims, provider cleanup owner, and atomic release
selector.  If a switch or health check fails, the controller deliberately keeps
the fence held until a caller proves a healthy stage and explicitly releases it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

_SHA256 = set("0123456789abcdef")
_STAGES = ("baseline", "candidate", "baseline", "candidate")


class MaintenanceWindowError(RuntimeError):
    """The maintenance sequence cannot safely advance or release its fence."""


class MaintenanceAdapter(Protocol):
    """Real shared-state operations required by a maintenance window.

    Implementations must make ``token`` a durable, cross-process admission
    fence.  The controller never substitutes an asyncio lock or process-local
    boolean for these operations.
    """

    async def close_admissions(self) -> str:
        """Atomically close new Run admissions and return an opaque lease token."""

    async def settle_and_fence_active_runs(self, token: str) -> None:
        """Settle or fence every active Run/worker under the admission lease."""

    async def confirm_provider_cleanup(self, token: str) -> None:
        """Confirm provider cleanup for settled Runs and workers."""

    async def observe_quiescence(self, token: str) -> QuiescenceObservation:
        """Re-read quiescence while the admission fence is held."""

    async def switch_complete_bundle(self, token: str, bundle_sha256: str) -> None:
        """Atomically select the complete release bundle."""

    async def verify_durable_continuity(self, token: str, bundle_sha256: str) -> ContinuityObservation:
        """Verify history, workspace, artifacts, and a new API/SSE Turn."""

    async def verify_stage_health(self, token: str, bundle_sha256: str) -> None:
        """Prove the switched stage is healthy before releasing admissions."""

    async def release_admissions(self, token: str) -> None:
        """Release the exact fence lease after stage health is proven."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in _SHA256 for char in value):
        raise MaintenanceWindowError(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class QuiescenceObservation:
    """Sanitized state observed while the real fence is held."""

    admissions_closed: bool
    active_runs: int
    active_workers: int
    pending_cleanup: int
    provider_cleanup_confirmed: bool
    database_compatibility_sha256: str

    def __post_init__(self) -> None:
        if type(self.admissions_closed) is not bool or type(self.provider_cleanup_confirmed) is not bool:
            raise MaintenanceWindowError("quiescence booleans are invalid")
        for field in ("active_runs", "active_workers", "pending_cleanup"):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise MaintenanceWindowError(f"quiescence {field} is invalid")
        _require_sha256(self.database_compatibility_sha256, "database_compatibility_sha256")

    def require_safe(self, *, database_compatibility_sha256: str) -> None:
        """Reject any open admission, active owner, cleanup, or mismatched proof."""
        _require_sha256(database_compatibility_sha256, "database_compatibility_sha256")
        if (
            self.admissions_closed is not True
            or self.active_runs != 0
            or self.active_workers != 0
            or self.pending_cleanup != 0
            or self.provider_cleanup_confirmed is not True
            or self.database_compatibility_sha256 != database_compatibility_sha256
        ):
            raise MaintenanceWindowError("maintenance window is not quiescent and compatible")

    @property
    def observation_sha256(self) -> str:
        """Return a deterministic digest of the quiescence fields."""
        return _digest(
            {
                "admissions_closed": self.admissions_closed,
                "active_runs": self.active_runs,
                "active_workers": self.active_workers,
                "pending_cleanup": self.pending_cleanup,
                "provider_cleanup_confirmed": self.provider_cleanup_confirmed,
                "database_compatibility_sha256": self.database_compatibility_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class ContinuityObservation:
    """Checksums returned by the adapter after one bundle switch."""

    session_history_sha256: str
    workspace_sha256: str
    artifacts_sha256: str
    new_turn_sha256: str
    provider_cleanup_confirmed: bool = True
    durable_continuity: bool = True

    def __post_init__(self) -> None:
        for field in ("session_history_sha256", "workspace_sha256", "artifacts_sha256", "new_turn_sha256"):
            _require_sha256(getattr(self, field), field)
        if type(self.provider_cleanup_confirmed) is not bool or type(self.durable_continuity) is not bool:
            raise MaintenanceWindowError("continuity booleans are invalid")

    def require_passed(self) -> None:
        """Reject continuity evidence unless cleanup and durability are confirmed."""
        if self.provider_cleanup_confirmed is not True or self.durable_continuity is not True:
            raise MaintenanceWindowError("durable continuity or provider cleanup is not confirmed")


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    """Non-secret receipt for one complete, fenced release transition."""

    stage: str
    bundle_sha256: str
    before_observation_sha256: str
    after_observation_sha256: str
    continuity: ContinuityObservation

    def __post_init__(self) -> None:
        if self.stage not in {"baseline", "candidate"}:
            raise MaintenanceWindowError("transition stage is invalid")
        _require_sha256(self.bundle_sha256, "bundle_sha256")
        _require_sha256(self.before_observation_sha256, "before_observation_sha256")
        _require_sha256(self.after_observation_sha256, "after_observation_sha256")

    def public_payload(self) -> dict[str, Any]:
        """Return the non-secret transition fields with an integrity digest."""
        unsigned = {
            "stage": self.stage,
            "bundle_sha256": self.bundle_sha256,
            "before_observation_sha256": self.before_observation_sha256,
            "after_observation_sha256": self.after_observation_sha256,
            "session_history_sha256": self.continuity.session_history_sha256,
            "workspace_sha256": self.continuity.workspace_sha256,
            "artifacts_sha256": self.continuity.artifacts_sha256,
            "new_turn_sha256": self.continuity.new_turn_sha256,
            "provider_cleanup_confirmed": self.continuity.provider_cleanup_confirmed,
            "durable_continuity": self.continuity.durable_continuity,
        }
        return {**unsigned, "transition_sha256": _digest(unsigned)}


class MaintenanceWindowController:
    """Run complete bundle switches while retaining the real fence lease."""

    def __init__(self, adapter: MaintenanceAdapter) -> None:
        self._adapter = adapter
        self._fence_token: str | None = None
        self._failed_bundle_sha256: str | None = None

    @property
    def fence_held(self) -> bool:
        """Return whether the controller currently retains the admission fence."""
        return self._fence_token is not None

    async def switch(
        self,
        *,
        stage: str,
        bundle_sha256: str,
        database_compatibility_sha256: str,
    ) -> TransitionReceipt:
        """Perform close → settle → cleanup → re-read → switch → verify → release.

        A failure at any step raises :class:`MaintenanceWindowError` and leaves
        the admission fence held for :meth:`release_after_recovery`.
        """
        if stage not in {"baseline", "candidate"}:
            raise MaintenanceWindowError("transition stage is invalid")
        _require_sha256(bundle_sha256, "bundle_sha256")
        _require_sha256(database_compatibility_sha256, "database_compatibility_sha256")
        if self._fence_token is not None:
            raise MaintenanceWindowError("another maintenance transition already holds the fence")
        token = await self._adapter.close_admissions()
        if not isinstance(token, str) or not token.strip() or len(token) > 1024:
            raise MaintenanceWindowError("admission adapter returned an invalid fence token")
        self._fence_token = token
        self._failed_bundle_sha256 = bundle_sha256
        try:
            await self._adapter.settle_and_fence_active_runs(token)
            await self._adapter.confirm_provider_cleanup(token)
            before = await self._adapter.observe_quiescence(token)
            if not isinstance(before, QuiescenceObservation):
                raise MaintenanceWindowError("admission adapter returned invalid quiescence evidence")
            before.require_safe(database_compatibility_sha256=database_compatibility_sha256)
            await self._adapter.switch_complete_bundle(token, bundle_sha256)
            continuity = await self._adapter.verify_durable_continuity(token, bundle_sha256)
            if not isinstance(continuity, ContinuityObservation):
                raise MaintenanceWindowError("admission adapter returned invalid continuity evidence")
            continuity.require_passed()
            after = await self._adapter.observe_quiescence(token)
            if not isinstance(after, QuiescenceObservation):
                raise MaintenanceWindowError("admission adapter returned invalid post-switch evidence")
            after.require_safe(database_compatibility_sha256=database_compatibility_sha256)
            await self._adapter.verify_stage_health(token, bundle_sha256)
            await self._adapter.release_admissions(token)
        except BaseException as exc:
            # The adapter fence remains held.  Releasing here would allow new
            # Runs into a stage whose continuity or health has not been proven.
            raise MaintenanceWindowError("maintenance transition blocked; admission fence remains held") from exc
        else:
            self._fence_token = None
            self._failed_bundle_sha256 = None
            return TransitionReceipt(
                stage=stage,
                bundle_sha256=bundle_sha256,
                before_observation_sha256=before.observation_sha256,
                after_observation_sha256=after.observation_sha256,
                continuity=continuity,
            )

    async def release_after_recovery(self, *, bundle_sha256: str, database_compatibility_sha256: str) -> None:
        """Release a held failed-transition fence only after health is re-proven."""
        _require_sha256(bundle_sha256, "bundle_sha256")
        if self._fence_token is None or self._failed_bundle_sha256 != bundle_sha256:
            raise MaintenanceWindowError("no matching failed transition fence is held")
        token = self._fence_token
        observation = await self._adapter.observe_quiescence(token)
        observation.require_safe(database_compatibility_sha256=database_compatibility_sha256)
        await self._adapter.verify_stage_health(token, bundle_sha256)
        await self._adapter.release_admissions(token)
        self._fence_token = None
        self._failed_bundle_sha256 = None

    async def rehearse(
        self,
        *,
        baseline_bundle_sha256: str,
        candidate_bundle_sha256: str,
        database_compatibility_sha256: str,
    ) -> tuple[TransitionReceipt, ...]:
        """Run exactly baseline → candidate → baseline → candidate."""
        _require_sha256(baseline_bundle_sha256, "baseline_bundle_sha256")
        _require_sha256(candidate_bundle_sha256, "candidate_bundle_sha256")
        if baseline_bundle_sha256 == candidate_bundle_sha256:
            raise MaintenanceWindowError("rehearsal requires distinct bundle identities")
        receipts: list[TransitionReceipt] = []
        for stage in _STAGES:
            receipts.append(
                await self.switch(
                    stage=stage,
                    bundle_sha256=baseline_bundle_sha256 if stage == "baseline" else candidate_bundle_sha256,
                    database_compatibility_sha256=database_compatibility_sha256,
                )
            )
        return tuple(receipts)


__all__ = [
    "ContinuityObservation",
    "MaintenanceAdapter",
    "MaintenanceWindowController",
    "MaintenanceWindowError",
    "QuiescenceObservation",
    "TransitionReceipt",
]
