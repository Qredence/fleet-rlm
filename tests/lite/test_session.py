from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from fleet_rlm.session import SessionRuntime


def _runtime(**overrides) -> SessionRuntime:
    dependencies = {
        "sandbox_mgr": SimpleNamespace(),
        "store": SimpleNamespace(),
        "skill_mgr": SimpleNamespace(),
        "engine": SimpleNamespace(),
    }
    dependencies.update(overrides)
    return SessionRuntime(**dependencies)


def test_lost_session_lease_cancels_remote_work_and_fences_runtime() -> None:
    probed = threading.Event()
    cancelled = threading.Event()

    class LostLease:
        def execute(self, _statement: str) -> None:
            probed.set()
            raise ConnectionError("lease connection dropped")

        def close(self) -> None:
            return None

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(request_cancellation=cancelled.set),
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
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(request_cancellation=cancelled.set, session_id="s1"),
        store=store,
        lease=DroppedLease(),  # type: ignore[arg-type]
    )
    runtime.start_lease_monitor(interval_seconds=0.001)
    assert renewed_probe.wait(timeout=1)
    assert runtime.lease is renewed and not runtime.lease_lost.is_set() and not cancelled.is_set()
    runtime.assert_lease_held()
    runtime.close_lease()


def _registry_with(runtime, *, max_active_sessions: int = 64):
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    registry = SessionRuntimeRegistry(
        FleetSettings(_env_file=None, max_active_sessions=max_active_sessions),
        None,
    )
    registry._sessions["s1"] = runtime
    return registry


def _buildable_registry(runtime, *, max_active_sessions: int = 64):
    registry = _registry_with(runtime, max_active_sessions=max_active_sessions)
    registry.store = SimpleNamespace()  # type: ignore[assignment]
    registry.root_lm = object()
    return registry


def _stale_runtime(events: list[str]) -> SessionRuntime:
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown")),
        lease=SimpleNamespace(close=lambda: events.append("lease")),  # type: ignore[arg-type]
    )
    runtime.lease_lost.set()
    return runtime


def test_get_returns_a_healthy_runtime_without_evicting_it() -> None:
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: pytest.fail("healthy runtimes must not be evicted"))
    )
    registry = _registry_with(runtime)
    assert registry.get("s1") is runtime


def test_get_rebuilds_a_runtime_after_lease_loss(monkeypatch, caplog) -> None:
    events: list[str] = []
    stale = _stale_runtime(events)
    registry = _buildable_registry(stale)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)

    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]
    assert registry._sessions["s1"] is fresh
    assert "ownership lease was lost" in caplog.text


def test_get_keeps_a_stale_runtime_while_its_turn_is_still_draining(monkeypatch) -> None:
    from fleet_rlm import session as session_module

    monkeypatch.setattr(session_module, "_EVICT_WAIT_SECONDS", 0.01)
    events: list[str] = []
    stale = _stale_runtime(events)
    stale.turn_lock.acquire()
    registry = _buildable_registry(stale)
    monkeypatch.setattr(registry, "_build", lambda _session_id: pytest.fail("must not rebuild while draining"))
    try:
        with pytest.raises(RuntimeError, match="still draining"):
            registry.get("s1")
    finally:
        stale.turn_lock.release()
    assert registry._sessions["s1"] is stale
    assert events == []

    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]


def test_get_serves_a_cached_session_while_another_session_builds(monkeypatch) -> None:
    cached = _runtime()
    registry = _buildable_registry(cached)
    fresh = _runtime()
    build_started = threading.Event()
    release_build = threading.Event()

    def build(_session_id: str) -> SessionRuntime:
        build_started.set()
        assert release_build.wait(timeout=2)
        return fresh

    monkeypatch.setattr(registry, "_build", build)
    built: list[SessionRuntime] = []
    build_errors: list[BaseException] = []
    served: list[SessionRuntime] = []
    read_errors: list[BaseException] = []

    def build_second_session() -> None:
        try:
            built.append(registry.get("s2"))
        except BaseException as exc:
            build_errors.append(exc)

    def read_cached_session() -> None:
        try:
            served.append(registry.get("s1"))
        except BaseException as exc:
            read_errors.append(exc)

    builder = threading.Thread(target=build_second_session)
    reader = threading.Thread(target=read_cached_session)
    builder.start()
    try:
        assert build_started.wait(timeout=1)
        reader.start()
        reader.join(timeout=1)
        assert not reader.is_alive(), "a cached Session was blocked by another Session's build"
        assert read_errors == []
        assert served == [cached]
    finally:
        release_build.set()
        builder.join(timeout=2)
        reader.join(timeout=2)
    assert not builder.is_alive()
    assert build_errors == []
    assert built == [fresh]
    assert registry._sessions == {"s1": cached, "s2": fresh}


def test_get_refuses_a_new_session_at_capacity(monkeypatch) -> None:
    cached = _runtime()
    registry = _buildable_registry(cached, max_active_sessions=1)
    monkeypatch.setattr(registry, "_build", lambda _session_id: pytest.fail("must not build at capacity"))

    with pytest.raises(RuntimeError, match="maximum number of active Sessions"):
        registry.get("s2")
    assert registry._sessions == {"s1": cached}


def test_get_rebuilds_a_stale_session_at_capacity(monkeypatch) -> None:
    events: list[str] = []
    stale = _stale_runtime(events)
    registry = _buildable_registry(stale, max_active_sessions=1)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)

    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]
    assert registry._sessions == {"s1": fresh}


def test_evicting_a_session_stops_its_sandbox_releases_the_lease_and_forgets_the_runtime() -> None:
    events: list[str] = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown")),
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
    runtime = _runtime(sandbox_mgr=SimpleNamespace(shutdown=lambda: pytest.fail("must not shut down mid-Turn")))
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

    runtime = _runtime(sandbox_mgr=SimpleNamespace(shutdown=shutdown))
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

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1"),
        store=SimpleNamespace(acquire_session_lease=acquire),
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


def test_build_wires_the_durable_environment_marker_into_the_sandbox_manager(monkeypatch) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    captured: dict[str, object] = {}

    class RecordingManager:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)

    def claim(_session_id: str) -> bool:
        return True

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(execute=lambda _statement: None, close=lambda: None),
        runtime_settings=lambda _session_id: {},
        claim_environment_report=claim,
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "AdaptiveFleetEngine", lambda **_kwargs: SimpleNamespace())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    runtime = registry._build("s1")

    assert captured["environment_report_claim"] is claim
    runtime.close_lease()
