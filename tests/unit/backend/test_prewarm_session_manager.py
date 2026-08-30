"""DaytonaSessionManager.prewarm_session contracts.

Pre-warm acquires a lease through the normal admission path (creating the
Sandbox, canonical layout, and persisted binding), then releases the
interpreter lease immediately. The Sandbox keeps running and the binding
stays persisted so the first real Turn reuses the bound Sandbox; a
pre-warm failure surfaces to the caller and holds no lease. A failed
interpreter release surfaces to the caller and stays drain-retryable while
retaining the pre-warm's admission, sandbox ownership, and per-session claim.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from fleet_rlm.daytona.errors import ProviderRequestError
from fleet_rlm.daytona.session_manager import PREWARM_RUN_ID, LeaseRequest, get_active_lease_registry
from fleet_rlm.runtime.bindings import SandboxBinding
from tests.unit.backend.test_session_manager import _manager


class _FailOnceBackend:
    """Interpreter backend whose first close() fails and later ones settle."""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("interpreter backend close failed")


def _attach_failing_backend(platform) -> _FailOnceBackend:
    """Attach one fail-once interpreter backend to every created sandbox."""
    backend = _FailOnceBackend()
    original_create = platform.create

    async def create_with_failing_backend(**kwargs):
        sandbox = await original_create(**kwargs)
        sandbox.backend = backend
        return sandbox

    platform.create = create_with_failing_backend  # type: ignore[method-assign]
    return backend


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
    assert get_active_lease_registry().holder(session_id, workspace_id=workspace_id) is None
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
async def test_overlapping_prewarm_yields_before_competing_sandbox_create() -> None:
    mgr, platform, store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    first_create_started = asyncio.Event()
    allow_first_create = asyncio.Event()
    original_create = platform.create

    async def gated_create(**kwargs):
        first_create_started.set()
        await asyncio.wait_for(allow_first_create.wait(), timeout=10)
        return await original_create(**kwargs)

    platform.create = gated_create  # type: ignore[method-assign]

    first = asyncio.create_task(mgr.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id))
    await asyncio.wait_for(first_create_started.wait(), timeout=5)

    second = await asyncio.wait_for(
        mgr.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id),
        timeout=5,
    )
    assert second is False
    assert not platform.created, "overlapping pre-warm must yield before creating a competing Sandbox"

    allow_first_create.set()
    assert await asyncio.wait_for(first, timeout=10) is True
    assert len(platform.created) == 1
    assert isinstance(await store.get(session_id), SandboxBinding)
    assert not mgr.has_pending_ownership


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
async def test_prewarm_release_failure_is_not_reported_as_success() -> None:
    """A failed pre-warm release retains ownership and surfaces failure."""
    mgr, platform, store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    backend = _attach_failing_backend(platform)

    with pytest.raises(RuntimeError, match="interpreter backend close failed"):
        await mgr.prewarm_session(
            session_id, user_id=user_id, workspace_id=workspace_id, deadline=asyncio.get_running_loop().time() + 10
        )

    # The warm binding persists, but it is not reported as successfully
    # released while its interpreter owner remains live.
    binding = await store.get(session_id)
    assert isinstance(binding, SandboxBinding)
    assert binding.provider_state == "running"
    assert backend.close_calls == 1, "pre-warm release must have attempted interpreter shutdown once"
    assert get_active_lease_registry().holder(session_id, workspace_id=workspace_id) == PREWARM_RUN_ID
    assert mgr.has_pending_ownership, "pre-warm lease must remain retryable at drain"

    assert await mgr.aclose(drain_seconds=5.0) is True
    assert backend.close_calls == 2


@pytest.mark.asyncio
async def test_prewarm_release_failure_settles_through_drain_retry() -> None:
    """The failed pre-warm lease stays drain-retryable after the claim clear.

    aclose retries the retained release; the fail-once backend settles on
    its second close, and the manager ends with no pending ownership.
    """
    mgr, platform, _store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    backend = _attach_failing_backend(platform)

    with pytest.raises(RuntimeError, match="interpreter backend close failed"):
        await mgr.prewarm_session(
            session_id, user_id=user_id, workspace_id=workspace_id, deadline=asyncio.get_running_loop().time() + 10
        )

    assert backend.close_calls == 1
    # The retry owner keeps the admission permit and PREWARM claim until the
    # interpreter shutdown succeeds.
    assert get_active_lease_registry().holder(session_id, workspace_id=workspace_id) == PREWARM_RUN_ID
    assert mgr.has_pending_ownership, "failed pre-warm release must stay owned until drain"

    settled = await mgr.aclose(drain_seconds=5.0)

    assert settled is True
    assert backend.close_calls == 2, "drain must retry the failed interpreter shutdown"
    assert get_active_lease_registry().holder(session_id, workspace_id=workspace_id) is None
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


@pytest.mark.asyncio
async def test_real_turn_prewarm_claim_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PREWARM claim cannot consume a real Turn's longer deadline."""
    from fleet_rlm.daytona import session_manager

    mgr, _platform, _store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    registry = get_active_lease_registry()
    registry.acquire(session_id, session_manager.PREWARM_RUN_ID, workspace_id=workspace_id)
    monkeypatch.setattr(session_manager, "_PREWARM_CLAIM_WAIT_SECONDS", 0.05)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        with pytest.raises(session_manager.DaytonaLeaseAcquisitionTimeoutError):
            await mgr.acquire(
                LeaseRequest(
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=uuid4(),
                ),
                deadline=started + 10.0,
            )
    finally:
        registry.release(session_id, session_manager.PREWARM_RUN_ID, workspace_id=workspace_id)

    assert 0.04 <= loop.time() - started < 1.0


@pytest.mark.asyncio
async def test_expired_turn_deadline_does_not_wait_for_prewarm_claim() -> None:
    """An already-expired caller deadline still times out immediately."""
    from fleet_rlm.daytona import session_manager

    mgr, _platform, _store, _volumes = _manager()
    session_id, user_id, workspace_id = uuid4(), uuid4(), uuid4()
    registry = get_active_lease_registry()
    registry.acquire(session_id, session_manager.PREWARM_RUN_ID, workspace_id=workspace_id)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        with pytest.raises(session_manager.DaytonaLeaseAcquisitionTimeoutError):
            await mgr.acquire(
                LeaseRequest(
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=uuid4(),
                ),
                deadline=started - 1.0,
            )
    finally:
        registry.release(session_id, session_manager.PREWARM_RUN_ID, workspace_id=workspace_id)

    assert loop.time() - started < 0.1
