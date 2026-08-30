"""DaytonaSessionManager.prewarm_session contracts.

Pre-warm acquires a lease through the normal admission path (creating the
Sandbox, canonical layout, and persisted binding), then releases the
interpreter lease immediately. The Sandbox keeps running and the binding
stays persisted so the first real Turn reuses the bound Sandbox; a
pre-warm failure surfaces to the caller and holds no lease.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from fleet_rlm.daytona.errors import ProviderRequestError
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.runtime.bindings import SandboxBinding
from tests.unit.backend.test_session_manager import _manager


@pytest.mark.asyncio
async def test_prewarm_persists_binding_and_leaves_sandbox_running() -> None:
    mgr, platform, store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()

    result = await mgr.prewarm_session(
        session_id, user_id=user_id, workspace_id=workspace_id, deadline=asyncio.get_running_loop().time() + 10
    )

    assert result is True
    binding = await store.get(session_id)
    assert isinstance(binding, SandboxBinding)
    assert binding.provider_state == "running"
    # The Sandbox stays running (release never deletes) and is reusable.
    assert not mgr.has_pending_ownership
    # A follow-up acquisition reuses the same bound sandbox instead of creating.
    lease = await mgr.acquire(
        LeaseRequest(session_id=session_id, user_id=user_id, workspace_id=workspace_id),
        deadline=asyncio.get_running_loop().time() + 10,
    )
    try:
        assert lease.sandbox_id == binding.sandbox_id
        assert len(platform.created) == 1, "first real Turn must not create a second Sandbox"
    finally:
        await mgr.release(lease)


@pytest.mark.asyncio
async def test_prewarm_failure_surfaces_to_caller() -> None:
    mgr, _platform, _store, _volumes = _manager()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("provider create failed")

    mgr._platform.create = boom  # type: ignore[method-assign]

    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    # Provider failures cross the sanitization boundary: the raw RuntimeError
    # surfaces as a mapped ProviderRequestError to Fleet callers.
    with pytest.raises(ProviderRequestError, match="provider create failed"):
        await mgr.prewarm_session(
            session_id, user_id=user_id, workspace_id=workspace_id, deadline=asyncio.get_running_loop().time() + 10
        )
    assert not mgr.has_pending_ownership


@pytest.mark.asyncio
async def test_prewarm_yields_when_real_turn_acquires_concurrently() -> None:
    """A real Turn in flight supersedes the pre-warm; neither fails.

    The per-session active-lease claim means the pre-warm's synthetic
    acquisition conflicts with a real Turn that has registered its claim.
    The pre-warm must yield (return False) without disturbing the Turn,
    and the Turn must succeed and own a clean manager state.
    """
    mgr, _platform, _store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()

    from fleet_rlm.daytona.session_manager import PREWARM_RUN_ID, LeaseRequest

    prewarm_entered = asyncio.Event()
    release_prewarm = asyncio.Event()
    original_acquire = mgr.acquire

    async def gated_acquire(request, *, deadline=None, force_new=False):
        if request.run_id == PREWARM_RUN_ID:
            # Pre-warm's synthetic acquisition: pause before claiming the
            # session lease so the real Turn can register first.
            prewarm_entered.set()
            await asyncio.wait_for(release_prewarm.wait(), timeout=10)
        return await original_acquire(request, deadline=deadline, force_new=force_new)

    mgr.acquire = gated_acquire  # type: ignore[method-assign]

    prewarm_task = asyncio.create_task(mgr.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id))
    await asyncio.wait_for(prewarm_entered.wait(), timeout=5)

    # The real Turn completes its full acquisition (and claims the session
    # lease) while the pre-warm is paused ahead of its claim.
    real_lease = await original_acquire(
        LeaseRequest(session_id=session_id, user_id=user_id, workspace_id=workspace_id, run_id=uuid4()),
        deadline=asyncio.get_running_loop().time() + 10,
    )
    release_prewarm.set()

    result = await asyncio.wait_for(prewarm_task, timeout=10)
    assert result is False, "pre-warm must yield to the concurrent real Turn"
    await mgr.release(real_lease)
    # After the Turn releases, ownership must be fully settled: the yielded
    # pre-warm leaves no lingering acquisition, release, or sandbox owner.
    for _ in range(20):
        if not mgr.has_pending_ownership:
            break
        await asyncio.sleep(0.05)
    assert not mgr.has_pending_ownership, "pre-warm yield must not leave ownership pending after Turn release"


@pytest.mark.asyncio
async def test_real_turn_waits_out_inflight_prewarm_claim() -> None:
    """A real Turn arriving while a pre-warm holds the claim waits, not fails.

    The pre-warm claims the session lease under the reserved PREWARM_RUN_ID
    before its provider acquisition. A real Turn whose acquire conflicts
    with that holder must wait briefly for the pre-warm to settle and then
    complete its own acquisition — never surface ActiveLeaseConflictError
    from a pre-warm's claim.
    """
    from fleet_rlm.daytona.session_manager import LeaseRequest

    mgr, platform, _store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()

    prewarm_claimed = asyncio.Event()
    release_prewarm = asyncio.Event()
    original_create = platform.create

    async def gated_create(**kwargs):
        # The pre-warm has claimed the session lease; its provider creation
        # is in flight and holds the claim until the lease is released.
        prewarm_claimed.set()
        await asyncio.wait_for(release_prewarm.wait(), timeout=10)
        return await original_create(**kwargs)

    platform.create = gated_create  # type: ignore[method-assign]

    prewarm_task = asyncio.create_task(mgr.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id))
    await asyncio.wait_for(prewarm_claimed.wait(), timeout=5)

    # The pre-warm holds the claim (registry carries PREWARM_RUN_ID from
    # inside acquire). Start the real Turn; it must wait out the pre-warm.
    turn_task = asyncio.create_task(
        mgr.acquire(
            LeaseRequest(session_id=session_id, user_id=user_id, workspace_id=workspace_id, run_id=uuid4()),
            deadline=asyncio.get_running_loop().time() + 30,
        )
    )
    await asyncio.sleep(0.1)
    assert not turn_task.done(), "real Turn must wait for the pre-warm claim, not race past it"
    release_prewarm.set()

    lease = await asyncio.wait_for(turn_task, timeout=30)
    warm = await asyncio.wait_for(prewarm_task, timeout=30)
    assert lease.sandbox_id
    assert warm is True
    await mgr.release(lease)
    for _ in range(20):
        if not mgr.has_pending_ownership:
            break
        await asyncio.sleep(0.05)
    assert not mgr.has_pending_ownership
