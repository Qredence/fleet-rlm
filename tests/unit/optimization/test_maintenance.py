"""Maintenance controller keeps the real-fence ordering fail closed."""

from __future__ import annotations

import hashlib

import pytest

from fleet_rlm.optimization.maintenance import (
    ContinuityObservation,
    MaintenanceWindowController,
    MaintenanceWindowError,
    QuiescenceObservation,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class _Adapter:
    def __init__(self, *, fail_switch: bool = False) -> None:
        self.events: list[object] = []
        self.fail_switch = fail_switch
        self.token = "shared-fence-token"

    async def close_admissions(self) -> str:
        self.events.append("close")
        return self.token

    async def settle_and_fence_active_runs(self, token: str) -> None:
        self.events.append(("settle", token))

    async def confirm_provider_cleanup(self, token: str) -> None:
        self.events.append(("cleanup", token))

    async def observe_quiescence(self, token: str) -> QuiescenceObservation:
        self.events.append(("observe", token))
        return QuiescenceObservation(True, 0, 0, 0, True, _sha("db"))

    async def switch_complete_bundle(self, token: str, bundle_sha256: str) -> None:
        self.events.append(("switch", token, bundle_sha256))
        if self.fail_switch:
            raise RuntimeError("switch failed")

    async def verify_durable_continuity(self, token: str, bundle_sha256: str) -> ContinuityObservation:
        self.events.append(("continuity", token, bundle_sha256))
        return ContinuityObservation(_sha("history"), _sha("workspace"), _sha("artifacts"), _sha("turn"))

    async def verify_stage_health(self, token: str, bundle_sha256: str) -> None:
        self.events.append(("health", token, bundle_sha256))

    async def release_admissions(self, token: str) -> None:
        self.events.append(("release", token))


@pytest.mark.asyncio
async def test_switch_holds_fence_through_post_switch_health() -> None:
    adapter = _Adapter()
    controller = MaintenanceWindowController(adapter)
    receipt = await controller.switch(
        stage="candidate",
        bundle_sha256=_sha("candidate"),
        database_compatibility_sha256=_sha("db"),
    )

    assert receipt.stage == "candidate"
    assert controller.fence_held is False
    assert [event[0] if isinstance(event, tuple) else event for event in adapter.events] == [
        "close",
        "settle",
        "cleanup",
        "observe",
        "switch",
        "continuity",
        "observe",
        "health",
        "release",
    ]


@pytest.mark.asyncio
async def test_failed_switch_keeps_fence_until_explicit_healthy_recovery() -> None:
    adapter = _Adapter(fail_switch=True)
    controller = MaintenanceWindowController(adapter)
    bundle = _sha("candidate")
    with pytest.raises(MaintenanceWindowError, match="remains held"):
        await controller.switch(stage="candidate", bundle_sha256=bundle, database_compatibility_sha256=_sha("db"))
    assert controller.fence_held is True
    assert not any(isinstance(event, tuple) and event[0] == "release" for event in adapter.events)

    adapter.fail_switch = False
    await controller.release_after_recovery(bundle_sha256=bundle, database_compatibility_sha256=_sha("db"))
    assert controller.fence_held is False
    assert adapter.events[-1] == ("release", adapter.token)


@pytest.mark.asyncio
async def test_rehearsal_is_exactly_four_ordered_transitions() -> None:
    adapter = _Adapter()
    controller = MaintenanceWindowController(adapter)
    receipts = await controller.rehearse(
        baseline_bundle_sha256=_sha("baseline"),
        candidate_bundle_sha256=_sha("candidate"),
        database_compatibility_sha256=_sha("db"),
    )
    assert [receipt.stage for receipt in receipts] == ["baseline", "candidate", "baseline", "candidate"]
    switches = [event[2] for event in adapter.events if isinstance(event, tuple) and event[0] == "switch"]
    assert switches == [_sha("baseline"), _sha("candidate"), _sha("baseline"), _sha("candidate")]


@pytest.mark.asyncio
async def test_quiescence_mismatch_blocks_before_switch() -> None:
    adapter = _Adapter()
    adapter.observe_quiescence = lambda _token: None  # type: ignore[method-assign]
    controller = MaintenanceWindowController(adapter)
    with pytest.raises(MaintenanceWindowError, match="blocked"):
        await controller.switch(
            stage="baseline",
            bundle_sha256=_sha("baseline"),
            database_compatibility_sha256=_sha("db"),
        )
    assert controller.fence_held is True
