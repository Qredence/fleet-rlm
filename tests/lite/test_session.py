from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from fleet_rlm.session import SessionRuntime


def test_lost_session_lease_cancels_remote_work_and_fences_runtime() -> None:
    probed = threading.Event()
    cancelled = threading.Event()

    class LostLease:
        def execute(self, _statement: str) -> None:
            probed.set()
            raise ConnectionError("lease connection dropped")

        def close(self) -> None:
            return None

    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(cancel_turn=cancelled.set),
        harness=SimpleNamespace(),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
        lease=LostLease(),  # type: ignore[arg-type]
    )
    runtime.start_lease_monitor(interval_seconds=0.001)

    assert probed.wait(timeout=1)
    assert cancelled.wait(timeout=1)
    deadline = time.monotonic() + 1
    while not runtime.lease_lost.is_set() and time.monotonic() < deadline:
        time.sleep(0.001)

    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.assert_lease_held()

    runtime.close_lease()


def test_a_dropped_lease_is_renewed_on_a_fresh_connection_when_the_lock_is_still_free() -> None:
    renewed_probe = threading.Event()
    cancelled = threading.Event()

    class DroppedLease:
        def execute(self, _statement: str) -> None:
            raise ConnectionError("lease connection dropped")

        def close(self) -> None:
            return None

    class RenewedLease:
        def execute(self, _statement: str) -> None:
            renewed_probe.set()

        def close(self) -> None:
            return None

    renewed = RenewedLease()
    store = SimpleNamespace(acquire_session_lease=lambda session_id: renewed if session_id == "s1" else None)
    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(cancel_turn=cancelled.set, session_id="s1"),
        harness=SimpleNamespace(store=store),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
        lease=DroppedLease(),  # type: ignore[arg-type]
    )
    runtime.start_lease_monitor(interval_seconds=0.001)
    assert renewed_probe.wait(timeout=1)
    assert runtime.lease is renewed and not runtime.lease_lost.is_set() and not cancelled.is_set()
    runtime.assert_lease_held()
    runtime.close_lease()


def _registry_with(runtime):
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), None)
    registry._sessions["s1"] = runtime
    return registry


def test_evicting_a_session_stops_its_sandbox_releases_the_lease_and_forgets_the_runtime() -> None:
    events: list[str] = []
    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown")),
        harness=SimpleNamespace(),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
        lease=SimpleNamespace(close=lambda: events.append("lease")),  # type: ignore[arg-type]
    )
    registry = _registry_with(runtime)
    assert registry.evict("s1") is True
    assert events == ["shutdown", "lease"] and "s1" not in registry._sessions
    assert registry.evict("s1") is False
    assert not runtime.turn_lock.locked()


def test_evicting_a_busy_session_is_refused_and_keeps_the_runtime(monkeypatch) -> None:
    from fleet_rlm import session as session_module

    monkeypatch.setattr(session_module, "_EVICT_WAIT_SECONDS", 0.01)
    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: pytest.fail("must not shut down mid-Turn")),
        harness=SimpleNamespace(),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
    )
    registry = _registry_with(runtime)
    runtime.turn_lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            registry.evict("s1")
    finally:
        runtime.turn_lock.release()
    assert registry._sessions["s1"] is runtime


def test_eviction_fences_a_resolved_runtime_before_releasing_its_turn_lock() -> None:
    shutdown_started = threading.Event()
    finish_shutdown = threading.Event()
    rejected = threading.Event()

    def shutdown() -> None:
        shutdown_started.set()
        assert finish_shutdown.wait(timeout=2)

    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(shutdown=shutdown),
        harness=SimpleNamespace(),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
    )
    registry = _registry_with(runtime)
    stale = registry.get("s1")

    def use_stale_runtime() -> None:
        with stale.turn_lock:
            try:
                stale.assert_lease_held()
            except RuntimeError:
                rejected.set()

    eviction = threading.Thread(target=registry.evict, args=("s1",))
    eviction.start()
    try:
        assert shutdown_started.wait(timeout=1)
        assert runtime.lease_lost.is_set()
        waiting_turn = threading.Thread(target=use_stale_runtime)
        waiting_turn.start()
    finally:
        finish_shutdown.set()
        eviction.join(timeout=2)
    waiting_turn.join(timeout=2)
    assert rejected.is_set()
    assert not runtime.turn_lock.locked()

    from fastapi import HTTPException

    from fleet_rlm.api.deps import session_lock

    with pytest.raises(HTTPException) as unavailable, session_lock(stale):
        pytest.fail("An evicted runtime must not allow workspace changes")
    assert unavailable.value.status_code == 503
    assert not stale.turn_lock.locked()


def test_lease_acquired_after_close_times_out_is_closed_without_publication(monkeypatch) -> None:
    from fleet_rlm import session as session_module

    monkeypatch.setattr(session_module, "_LEASE_MONITOR_JOIN_SECONDS", 0.01)
    acquiring = threading.Event()
    finish_acquiring = threading.Event()
    old_closed = threading.Event()
    renewed_closed = threading.Event()
    renewed = SimpleNamespace(close=renewed_closed.set)

    def probe(_statement: str) -> None:
        raise ConnectionError("lease dropped")

    def acquire(_session_id: str):
        acquiring.set()
        assert finish_acquiring.wait(timeout=2)
        return renewed

    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(session_id="s1"),
        harness=SimpleNamespace(store=SimpleNamespace(acquire_session_lease=acquire)),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
        lease=SimpleNamespace(execute=probe, close=old_closed.set),  # type: ignore[arg-type]
    )
    runtime.start_lease_monitor(interval_seconds=0.001)
    monitor = runtime._lease_monitor
    try:
        assert acquiring.wait(timeout=1)
        runtime.close_lease()
        assert old_closed.is_set() and runtime.lease is None
        assert monitor is not None and monitor.is_alive()
    finally:
        finish_acquiring.set()
        if monitor is not None:
            monitor.join(timeout=2)
    assert renewed_closed.is_set() and runtime.lease is None
    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.assert_lease_held()
