"""Process-wide Daytona Interpreter Lease admission behavior."""

from __future__ import annotations

import asyncio

import pytest


def test_admission_rejects_more_than_eight_direct_leases() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmission

    with pytest.raises(ValueError, match="at most 8"):
        DaytonaAdmission(max_active_leases=9)


@pytest.mark.asyncio
async def test_eight_leases_enter_and_ninth_waits_until_release() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmission

    admission = DaytonaAdmission(max_active_leases=8)
    deadline = asyncio.get_running_loop().time() + 10
    permits = [await admission.acquire(deadline=deadline) for _ in range(8)]

    ninth = asyncio.create_task(admission.acquire(deadline=deadline))
    await asyncio.sleep(0)
    assert not ninth.done()

    permits[0].release()
    ninth_permit = await asyncio.wait_for(ninth, timeout=1)
    ninth_permit.release()
    for permit in permits[1:]:
        permit.release()


@pytest.mark.asyncio
async def test_cancelled_waiter_restores_capacity() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmission

    admission = DaytonaAdmission(max_active_leases=1)
    deadline = asyncio.get_running_loop().time() + 10
    held = await admission.acquire(deadline=deadline)
    waiter = asyncio.create_task(admission.acquire(deadline=deadline))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    held.release()

    replacement = await admission.acquire(deadline=deadline)
    replacement.release()


@pytest.mark.asyncio
async def test_deadline_exhaustion_does_not_consume_capacity() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionTimeout

    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    held = await admission.acquire(deadline=loop.time() + 10)

    with pytest.raises(DaytonaAdmissionTimeout, match="Daytona admission unavailable"):
        await admission.acquire(deadline=loop.time())

    held.release()
    replacement = await admission.acquire(deadline=loop.time() + 10)
    replacement.release()


@pytest.mark.asyncio
async def test_permit_release_is_idempotent() -> None:
    from fleet_rlm.daytona.session_manager import DaytonaAdmission

    admission = DaytonaAdmission(max_active_leases=1)
    deadline = asyncio.get_running_loop().time() + 10
    permit = await admission.acquire(deadline=deadline)
    permit.release()
    permit.release()

    replacement = await admission.acquire(deadline=deadline)
    replacement.release()
