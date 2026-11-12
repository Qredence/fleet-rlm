"""Session leases and history, agent identities, usage accounting, and observability."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.utils.usage_tracker import UsageTracker

from fleet_rlm import observability as obs
from fleet_rlm.observability import LMUsageCallback, _usage_summary, decision_usage, usage_callbacks
from fleet_rlm.session import SessionRuntime

# Session ownership and history


def _runtime(**overrides) -> SessionRuntime:
    dependencies = {
        "sandbox_mgr": SimpleNamespace(),
        "store": SimpleNamespace(),
        "skill_mgr": SimpleNamespace(),
        "engine": SimpleNamespace(),
    }
    dependencies.update(overrides)
    return SessionRuntime(**dependencies)


@pytest.mark.parametrize("status", ["running", "failed", "completed"])
def test_required_claim_binds_ordered_intent_but_completed_receipts_survive_source_changes(status):
    import hashlib
    from dataclasses import replace

    from fleet_rlm.skills import captured_required_skill
    from fleet_rlm.store import IdempotencyConflictError

    def skill(name):
        return captured_required_skill(
            name, "workspace", (("SKILL.md", f"---\nname: {name}\ndescription: Test\n---\nInstructions".encode()),)
        )

    a, b = skill("alpha"), skill("beta")
    rows = {}

    def claim(_session, turn_id, key, digest):
        if key in rows:
            return rows[key]
        rows[key] = {"turn_id": turn_id, "task_sha256": digest, "status": status}
        return None

    runtime = _runtime(
        store=SimpleNamespace(claim_turn=claim, turn_by_key=lambda _session, key: rows.get(key)),
        sandbox_mgr=SimpleNamespace(session_id="s1"),
    )
    assert runtime.completed_turn("missing", "task", ("alpha", "beta")) is None
    assert runtime.claim_turn("first", "k", "task", requested_skills=("alpha", "beta"), required_skills=(a, b)) is None
    receipt = runtime.completed_turn("k", "task", ("alpha", "beta"))
    assert receipt == (rows["k"] if status == "completed" else None)
    assert (
        runtime.claim_turn("retry", "k", "task", requested_skills=("alpha", "beta"), required_skills=(a, b))["turn_id"]
        == "first"
    )
    for task, names, records in (
        ("other", ("alpha", "beta"), (a, b)),
        ("task", ("beta", "alpha"), (b, a)),
        ("task", ("alpha",), (a,)),
        ("task", (), ()),
    ):
        with pytest.raises(IdempotencyConflictError):
            runtime.claim_turn("different", "k", task, requested_skills=names, required_skills=records)
        if status == "completed":
            with pytest.raises(IdempotencyConflictError):
                runtime.completed_turn("k", task, names)
    for changed in (replace(a, source="bundled"), replace(a, sha256="c" * 64)):
        if status == "completed":
            assert (
                runtime.claim_turn(
                    "source", "k", "task", requested_skills=("alpha", "beta"), required_skills=(changed, b)
                )["turn_id"]
                == "first"
            )
        else:
            with pytest.raises(IdempotencyConflictError):
                runtime.claim_turn(
                    "source", "k", "task", requested_skills=("alpha", "beta"), required_skills=(changed, b)
                )
    runtime.claim_turn("legacy", "legacy-key", "task")
    assert rows["legacy-key"]["task_sha256"] == hashlib.sha256(b"task").hexdigest()
    with pytest.raises(IdempotencyConflictError):
        runtime.claim_turn("upgrade", "legacy-key", "task", requested_skills=("alpha",), required_skills=(a,))
    if status == "completed":
        with pytest.raises(IdempotencyConflictError):
            runtime.completed_turn("legacy-key", "task", ("alpha",))


def test_committed_completion_owns_nested_values_separately_from_record_and_store() -> None:
    saved = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: ["artifacts/out.txt", "artifacts/out.txt"]),
        store=SimpleNamespace(commit_turn=lambda *_args, **kw: saved.append(kw)),
    )
    record = {"turn_id": "t1", "answer": "verified", "budget_breakdown": {"routing": 1}}
    result = runtime.commit_turn("s1", record)
    stored = saved[0]["completion"][1]
    assert result == stored and result["turn_id"] == "t1"
    record["budget_breakdown"]["routing"] = 100
    assert result["budget_breakdown"] == stored["budget_breakdown"] == {"routing": 1}
    result["budget_breakdown"]["routing"] = 200
    result["artifacts"].clear()
    assert stored["budget_breakdown"] == {"routing": 1} and stored["artifacts"] == ["artifacts/out.txt"]


def test_commit_does_not_return_a_completion_when_storage_raises() -> None:
    def fail(*_args, **_kwargs):
        raise ConnectionError("commit acknowledgment lost")

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: []),
        store=SimpleNamespace(commit_turn=fail),
    )
    with pytest.raises(ConnectionError):
        runtime.commit_turn("s1", {"turn_id": "t1"})


def test_turn_intent_and_completion_are_fenced_by_the_runtime_ownership_epoch() -> None:
    claims = []
    commits = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1", list_workspace_files=lambda: []),
        store=SimpleNamespace(
            claim_turn=lambda *args, **kwargs: claims.append((args, kwargs)),
            commit_turn=lambda *args, **kwargs: commits.append((args, kwargs)),
        ),
        ownership_epoch=12,
    )

    runtime.claim_turn("turn-1", "key-1", "task")
    runtime.commit_turn("s1", {"turn_id": "turn-1"})

    assert claims[0][1] == {"ownership_epoch": 12}
    assert commits[0][1]["ownership_epoch"] == 12


@pytest.mark.parametrize("paths", [[], ["files/input.txt", "artifacts/out.txt", "artifacts/out.txt"], ["../escape"]])
def test_captured_artifact_view_uses_existing_path_validation_without_relisting(paths) -> None:
    saved = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: pytest.fail("captured view must not be relisted")),
        store=SimpleNamespace(commit_turn=lambda *_args, **kw: saved.append(kw)),
    )
    if "../escape" in paths:
        with pytest.raises(ValueError, match="invalid relative path"):
            runtime.commit_turn("s1", {"turn_id": "t1"}, artifact_paths=paths)
        assert saved == []
    else:
        result = runtime.commit_turn("s1", {"turn_id": "t1"}, artifact_paths=paths)
        assert result["artifacts"] == sorted({p for p in paths if p.startswith("artifacts/")})


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
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown") or True),
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


def test_get_releases_epoch_fenced_runtime_without_stopping_new_owner(monkeypatch) -> None:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    events = []
    manager = DaytonaSandboxManager("key", "org", "volume", "s1")
    manager._sandbox = SimpleNamespace(id="taken-over")
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: pytest.fail("stale provider stop"))
    manager.ownership_epoch = 1
    manager.ownership_resource_mutate = lambda *_args: False
    stale = _stale_runtime(events)
    stale.sandbox_mgr = manager
    registry = _buildable_registry(stale)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    assert registry.get("s1") is fresh
    assert registry._sessions["s1"] is fresh and events == ["lease"]
    assert manager._ownership_lost is True and manager._sandbox is None


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
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown") or True),
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


@pytest.mark.parametrize("raises", [False, True])
def test_unconfirmed_eviction_retains_runtime_and_lease_until_cleanup_succeeds(monkeypatch, raises) -> None:
    events: list[str] = []

    def unconfirmed() -> bool:
        events.append("shutdown")
        if raises:
            raise RuntimeError("provider refused cleanup")
        return False

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=unconfirmed),
        lease=SimpleNamespace(close=lambda: events.append("lease")),
    )
    registry = _buildable_registry(runtime)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    with pytest.raises(RuntimeError):
        registry.evict("s1")
    assert runtime.lease_lost.is_set()
    assert registry._sessions["s1"] is runtime
    assert events == ["shutdown"]
    with pytest.raises(RuntimeError):
        registry.get("s1")
    assert registry._sessions["s1"] is runtime
    assert events == ["shutdown", "shutdown"]
    runtime.sandbox_mgr.shutdown = lambda: events.append("confirmed") or True
    assert registry.get("s1") is fresh
    assert events == ["shutdown", "shutdown", "confirmed", "lease"]


def test_registry_close_attempts_every_runtime_and_retains_unresolved_ones() -> None:
    events: list[str] = []
    unresolved = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1", shutdown=lambda: False),
        lease=SimpleNamespace(close=lambda: events.append("unresolved-lease")),
    )
    healthy = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s2", shutdown=lambda: events.append("shutdown-s2") or True),
        lease=SimpleNamespace(close=lambda: events.append("healthy-lease")),
    )
    registry = _registry_with(unresolved)
    registry._sessions["s2"] = healthy
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        registry.close()
    assert registry._sessions == {"s1": unresolved}
    assert unresolved.lease_lost.is_set()
    assert events == ["shutdown-s2", "healthy-lease"]
    unresolved.sandbox_mgr.shutdown = lambda: True
    registry.close()
    assert not registry._sessions
    assert events[-1] == "unresolved-lease"


def test_registry_close_retries_failed_lease_release_and_attempts_other_runtimes() -> None:
    events: list[str] = []

    def failed_close() -> None:
        events.append("failed-lease")
        raise RuntimeError("connection close failed")

    lease = SimpleNamespace(close=failed_close)
    unresolved = _runtime(sandbox_mgr=SimpleNamespace(session_id="s1", shutdown=lambda: True), lease=lease)
    healthy = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s2", shutdown=lambda: True),
        lease=SimpleNamespace(close=lambda: events.append("healthy-lease")),
    )
    registry = _registry_with(unresolved)
    registry._sessions["s2"] = healthy
    with pytest.raises(RuntimeError, match="unconfirmed"):
        registry.close()
    assert events == ["failed-lease", "healthy-lease"]
    assert registry._sessions == {"s1": unresolved}
    assert unresolved.lease is lease
    lease.close = lambda: events.append("retried-lease")
    registry.close()
    assert unresolved.lease is None
    assert not registry._sessions
    assert events[-1] == "retried-lease"


def test_eviction_fences_a_resolved_runtime_before_releasing_its_turn_lock() -> None:
    shutdown_started = threading.Event()
    finish_shutdown = threading.Event()
    rejected = threading.Event()

    def shutdown() -> bool:
        shutdown_started.set()
        assert finish_shutdown.wait(timeout=2)
        return True

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
    calls: list[object] = []

    class RecordingManager:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            self.reconcile_sandbox_ownership = lambda _resources: calls.append("reconcile")

    def claim(_session_id: str) -> bool:
        return True

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(execute=lambda _statement: None, close=lambda: None),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": []},
        fail_interrupted_turns=lambda _session_id, epoch: calls.append(("fail-interrupted", epoch)) or ["old-turn"],
        runtime_settings=lambda _session_id: {},
        save_runtime_settings=lambda _session_id, value: calls.append(value),
        claim_environment_report=claim,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace(reload_program=lambda: None))
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    runtime = registry._build("s1")

    assert captured["environment_report_claim"] is claim
    assert calls == ["reconcile", ("fail-interrupted", 1)]
    runtime.close_lease()


def _global_program_registry(tmp_path, monkeypatch):
    import dspy

    from fleet_rlm.config import FleetSettings
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager
    from fleet_rlm.session import SessionRuntimeRegistry

    saved = {}
    ledger = {"approved": None, "approving": []}
    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(close=lambda: None),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": []},
        fail_interrupted_turns=lambda *_args: [],
        runtime_settings=lambda _session_id: dict(saved),
        save_runtime_settings=lambda _session_id, value: saved.update(value),
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: dict(ledger),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "reconcile_sandbox_ownership", lambda *_args: None)
    monkeypatch.setattr(DaytonaSandboxManager, "execution_evidence", lambda _self: {})
    monkeypatch.setattr(SessionRuntime, "start_lease_monitor", lambda _self: None)
    registry = SessionRuntimeRegistry(
        FleetSettings(
            _env_file=None,
            compiled_program_dir=str(tmp_path),
            fleet_daytona_api_key="key",
            fleet_daytona_org_id="org",
            fleet_daytona_volume_id="volume",
        ),
        store,
    )
    registry.root_lm = registry.sub_lm = dspy.LM("openai/offline")
    registry.ledger = ledger
    return registry, saved


def test_global_program_file_is_the_only_selection_for_every_session(tmp_path, monkeypatch) -> None:
    import dspy

    from fleet_rlm.engine.compiled import program_envelope

    registry, saved = _global_program_registry(tmp_path, monkeypatch)
    first = registry._build("s1")
    assert first.engine.compiled.digest == "" and first.engine.compiled_path == str(tmp_path / "fleet.json")
    candidate = first.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    (tmp_path / "fleet.json").write_bytes(program_envelope(candidate))
    monkeypatch.setattr(first.engine.program, "forward", lambda **_inputs: dspy.Prediction(answer="offline"))
    assert first.engine("", "", "task").answer == "offline"
    assert first.engine.compiled.digest
    # A different Session starts on the same installed program, and nothing is stored per Session.
    second = registry._build("s2")
    assert second.engine.compiled.digest == first.engine.compiled.digest
    assert second.engine.program.routing.predict.signature.instructions == "Approved policy"
    assert saved == {}
    # A live runtime that already serves the artifact refuses to continue if the file vanishes.
    (tmp_path / "fleet.json").unlink()
    with pytest.raises(FileNotFoundError):
        first.engine("", "", "task")
    # Retiring the file is the operator's reset: a fresh runtime starts on baseline.
    fresh = registry._build("s3")
    assert fresh.engine.compiled.digest == ""
    for runtime in (first, second, fresh):
        runtime.close_lease()


def _approved_bytes(registry) -> bytes:
    from fleet_rlm.engine.compiled import program_envelope

    runtime = registry._build("seed")
    candidate = runtime.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    runtime.close_lease()
    return program_envelope(candidate)


@pytest.mark.parametrize("problem", ["missing", "changed"])
def test_once_a_global_job_was_approved_a_missing_or_changed_program_refuses_every_turn(
    tmp_path, monkeypatch, problem
) -> None:
    import hashlib

    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    content = _approved_bytes(registry)
    registry.ledger["approved"] = hashlib.sha256(content).hexdigest()
    path = tmp_path / "fleet.json"
    path.write_bytes(content)
    live = registry._build("s1")
    assert live.engine.program.routing.predict.signature.instructions == "Approved policy"
    if problem == "missing":
        path.unlink()
    else:
        path.write_bytes(content + b" ")  # still valid JSON, but not what the approval installed
    with pytest.raises((ValueError, FileNotFoundError)):
        live.engine("", "", "task")
    # A brand new runtime, however old the process, enforces the same ledger instead of serving baseline.
    with pytest.raises(ValueError, match="approved global program"):
        registry._build("s2")
    live.close_lease()


def test_an_approval_in_flight_may_already_have_replaced_the_file(tmp_path, monkeypatch) -> None:
    import hashlib

    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    content = _approved_bytes(registry)
    registry.ledger.update(
        approved=hashlib.sha256(b"older approval").hexdigest(), approving=[hashlib.sha256(content).hexdigest()]
    )
    (tmp_path / "fleet.json").write_bytes(content)
    registry._build("s1").close_lease()
    registry.ledger["approving"] = []
    with pytest.raises(ValueError, match="approved global program"):
        registry._build("s2")


def test_per_session_artifacts_from_before_the_global_program_are_ignored(tmp_path, monkeypatch) -> None:
    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    (tmp_path / "s1.json").write_text("not a program at all")
    runtime = registry._build("s1")
    assert runtime.engine.compiled.digest == ""
    runtime.close_lease()


def test_invalid_global_program_refuses_the_session_instead_of_serving_baseline(tmp_path, monkeypatch) -> None:
    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    (tmp_path / "fleet.json").write_text('{"not": "a fleet-program-b1 envelope"}')
    with pytest.raises(ValueError, match="Unsupported compiled program"):
        registry._build("s1")


@pytest.mark.parametrize("approval_at", ["startup", "invocation"])
def test_concurrent_approval_is_served_without_any_per_session_selection(tmp_path, monkeypatch, approval_at) -> None:
    import dspy

    from fleet_rlm.engine.compiled import program_envelope

    registry, saved = _global_program_registry(tmp_path, monkeypatch)
    runtime = registry._build("s1")
    candidate = runtime.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    content = program_envelope(candidate)
    path = tmp_path / "fleet.json"

    if approval_at == "startup":
        runtime.close_lease()

        def approve() -> None:
            path.write_bytes(content)

        monkeypatch.setattr(
            registry.store,
            "acquire_session_lease",
            lambda _session_id: approve() or SimpleNamespace(close=lambda: None),
        )

        def admit():
            return registry._build("s1")
    else:
        reload = runtime.engine.compiled.reload_if_changed
        pending = True

        def reload_then_approve(*args, **kwargs) -> None:
            nonlocal pending
            reload(*args, **kwargs)
            if pending:
                pending = False
                path.write_bytes(content)

        monkeypatch.setattr(runtime.engine.compiled, "reload_if_changed", reload_then_approve)
        monkeypatch.setattr(runtime.engine.program, "forward", lambda **_inputs: dspy.Prediction(answer="offline"))

        def admit():
            runtime.engine("", "", "task")  # approval lands during this Turn's boundary check
            return runtime.engine("", "", "task")  # and is served by the next one

    try:
        admitted = admit()
        engine = admitted.engine if approval_at == "startup" else runtime.engine
        assert engine.program.routing.predict.signature.instructions == "Approved policy"
        if approval_at == "startup":
            admitted.close_lease()
        assert saved == {}
    finally:
        runtime.close_lease()


def test_build_does_not_terminalize_running_turns_when_resource_reconciliation_fails(monkeypatch) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    calls: list[str] = []

    class RecordingManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            def fail_reconcile(_resources: object) -> None:
                calls.append("reconcile")
                raise RuntimeError("provider cleanup remains uncertain")

            self.reconcile_sandbox_ownership = fail_reconcile

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(
            execute=lambda _statement: None,
            close=lambda: calls.append("lease-close"),
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: 2,
        sandbox_ownership=lambda _session_id: {"epoch": 2, "resources": [{"action": "stop"}]},
        fail_interrupted_turns=lambda *_args: calls.append("fail-interrupted"),
        runtime_settings=lambda _session_id: {},
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="provider cleanup remains uncertain"):
        registry._build("s1")

    assert calls == ["reconcile", "lease-close"]


@pytest.mark.parametrize(
    ("epoch", "ownership", "message"),
    [
        (None, None, "ownership is unknown"),
        (4, None, "cleanup is unresolved"),
    ],
)
def test_build_refuses_a_session_without_reconciled_sandbox_ownership(
    monkeypatch, epoch: int | None, ownership: dict | None, message: str
) -> None:
    """A Session predating the ownership record is fenced, not treated as having no compute."""
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    calls: list[str] = []

    class RecordingManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            # Any provider work here would mean the refusal happened too late.
            self.reconcile_sandbox_ownership = lambda _resources: calls.append("reconcile")

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(
            execute=lambda _statement: None,
            close=lambda: calls.append("lease-close"),
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: epoch,
        sandbox_ownership=lambda _session_id: ownership,
        fail_interrupted_turns=lambda *_args: calls.append("fail-interrupted"),
        runtime_settings=lambda _session_id: {},
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=message):
        registry._build("s1")

    assert calls == ["lease-close"]


@pytest.mark.parametrize(
    ("created", "prior_state", "handoff_state", "resources", "expected"),
    [
        (False, "known_empty", "unknown", [], "unknown"),
        (True, None, None, [], "known_empty"),
        (True, None, None, [{"action": "stop"}], "unknown"),
    ],
)
def test_build_initializes_and_refreshes_workspace_state_after_acquiring_the_lease(
    monkeypatch,
    created: bool,
    prior_state: str | None,
    handoff_state: str | None,
    resources: list[dict],
    expected: str,
) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    events: list[str] = []
    saved = {"workspace_state": prior_state} if prior_state is not None else {}

    def acquire(_session_id: str):
        events.append("acquire_lease")
        if handoff_state is not None:
            saved["workspace_state"] = handoff_state
        return SimpleNamespace(execute=lambda _statement: None, close=lambda: events.append("lease-close"))

    store = SimpleNamespace(
        acquire_session_lease=acquire,
        ensure_session=lambda _session_id: events.append("ensure_session") or created,
        runtime_settings=lambda _session_id: events.append("runtime_settings") or dict(saved),
        save_runtime_settings=lambda _session_id, value: (
            events.append(f"save:{value['workspace_state']}") or saved.update(value)
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": resources},
        fail_interrupted_turns=lambda *_args: [],
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(
        session_module,
        "DaytonaSandboxManager",
        lambda *_args, **kwargs: SimpleNamespace(
            session_id=kwargs["session_id"], reconcile_sandbox_ownership=lambda _resources: None
        ),
    )
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace(reload_program=lambda: None))
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    runtime = registry._build("s1")
    try:
        assert events[:3] == ["acquire_lease", "ensure_session", "runtime_settings"]
        assert events.count("runtime_settings") == 1
        assert runtime.workspace_state == saved["workspace_state"] == expected
    finally:
        runtime.close_lease()


def test_set_workspace_state_fences_when_session_lease_is_lost() -> None:
    from fleet_rlm.session import SessionRuntime

    writes: list[dict] = []
    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(session_id="s1"),  # type: ignore[arg-type]
        store=SimpleNamespace(save_runtime_settings=lambda _sid, data: writes.append(data)),  # type: ignore[arg-type]
        skill_mgr=SimpleNamespace(),  # type: ignore[arg-type]
        engine=SimpleNamespace(),  # type: ignore[arg-type]
        workspace_state="known_empty",
    )
    runtime.lease_lost.set()

    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.set_workspace_state("unknown")

    assert runtime.workspace_state == "known_empty" and writes == []


def test_registry_fences_admission_and_preserves_active_work():
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), None)
    lock = threading.Lock()
    registry._sessions["one"] = SimpleNamespace(turn_lock=lock)
    lock.acquire()
    assert not registry.request_restart()
    assert not registry._restart_requested.is_set()
    lock.release()
    assert registry.request_restart()
    with pytest.raises(RuntimeError, match="restarting"):
        registry.get("two")


@pytest.mark.parametrize("race", [False, True])
def test_workspace_lock_rejects_work_before_and_after_restart_fence(race):
    from fastapi import HTTPException

    from fleet_rlm.api.deps import session_lock

    restarting = threading.Event()
    lock = threading.Lock()

    class Lock:
        def acquire(self, **_kwargs):
            lock.acquire()
            if race:
                restarting.set()
            return True

        def release(self):
            lock.release()

    if not race:
        restarting.set()
    runtime = SimpleNamespace(turn_lock=Lock(), restart_requested=restarting, assert_lease_held=lambda: None)
    with pytest.raises(HTTPException) as error, session_lock(runtime):
        pytest.fail("workspace work must not start")
    assert error.value.status_code == 503
    assert not lock.locked()


# Agent identity


class Span:
    def __init__(self):
        self.attributes = {}

    def set_attributes(self, values):
        self.attributes.update(values)

    def set_attribute(self, key, value):
        self.attributes[key] = value


@pytest.fixture
def registry(monkeypatch):
    models, associations = [], []
    monkeypatch.setattr(
        obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "source", "fleet.agent.git_dirty": "true"}
    )
    monkeypatch.setattr(obs, "_AGENT_MODELS", {})
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://local.invalid")

    def search(**kwargs):
        return [m for m in models if m.tags["fleet.agent.fingerprint"] in kwargs["filter_string"]]

    def create(**kwargs):
        model = SimpleNamespace(model_id=f"m-{len(models)}", **kwargs)
        models.append(model)
        return model

    monkeypatch.setattr(obs.mlflow, "search_logged_models", search)
    monkeypatch.setattr(obs.mlflow, "create_external_model", create)
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: associations.append(kwargs))
    monkeypatch.setattr(obs.mlflow, "set_active_model", lambda **_kw: pytest.fail("Global model context changed"))
    return models, associations


def engine(instruction="first", credential="private"):
    return SimpleNamespace(dump_state=lambda: {"predict": {"signature": instruction, "lm": {"api_key": credential}}})


def test_reuses_version_without_uploading_state_and_excludes_credentials(registry):
    models, associations = registry
    span = Span()
    first = obs.associate_turn_agent(engine(), "config", "1", span)
    assert obs.associate_turn_agent(engine(credential="rotated"), "config", "1", Span()) == first
    assert len(models) == 1 and len(associations) == 2
    assert "private" not in str(models[0].tags) and "signature" not in str(models[0].tags)
    assert models[0].name.startswith("fleet-rlm-")
    assert span.attributes["fleet.agent.model_id"] == first
    obs._AGENT_MODELS.clear()
    assert obs.associate_turn_agent(engine(), "config", "1", Span()) == first
    assert len(models) == 1  # Native search reuse after the process cache is empty.


def test_benchmark_trace_uses_requested_model_and_retains_automatic_identity(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "m-benchmark")
    monkeypatch.setenv(
        "FLEET_MLFLOW_BENCHMARK_TAGS",
        '{"fleet.benchmark.campaign":"cold-start-2026-11","fleet.benchmark.condition":"baseline"}',
    )
    span = Span()

    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)

    assert associations[-1]["model_id"] == "m-benchmark"
    assert associations[-1]["tags"] == {
        "fleet.benchmark.campaign": "cold-start-2026-11",
        "fleet.benchmark.condition": "baseline",
    }
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.model_id"] == "m-benchmark"


def test_benchmark_tags_attach_without_benchmark_model_id(registry, monkeypatch):
    _, associations = registry
    tags = {"fleet.benchmark.campaign": "cold-start-2026-11"}
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.campaign":"cold-start-2026-11"}')
    span = Span()
    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic_model_id, "tags": tags}
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"
    assert "fleet.benchmark.model_id" not in span.attributes

    def fail(**_kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    unversioned = Span()
    assert obs.associate_turn_agent(engine("unversioned"), "config", "1", unversioned) is None
    assert associations[-1] == {"tags": tags}
    assert unversioned.attributes["fleet.agent.identity_status"] == "unversioned"
    assert unversioned.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"


def test_benchmark_env_strips_whitespace_and_rejects_empty_tag_suffix(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "   ")
    span = Span()
    automatic = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic}
    assert "fleet.benchmark.model_id" not in span.attributes

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "  m-bench  ")
    padded = Span()
    obs.associate_turn_agent(engine(), "config", "1", padded)
    assert associations[-1] == {"model_id": "m-bench"}
    assert padded.attributes["fleet.benchmark.model_id"] == "m-bench"

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.":"missing-name"}')
    invalid = Span()
    retained = obs.associate_turn_agent(engine(), "config", "1", invalid)
    assert retained is not None
    assert associations[-1]["model_id"] == "m-bench"
    assert invalid.attributes["fleet.agent.model_id"] == retained
    assert invalid.attributes["fleet.benchmark.model_id"] == "m-bench"
    assert "fleet.benchmark." not in invalid.attributes

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", "{not-json")
    malformed = Span()
    obs.associate_turn_agent(engine(), "config", "1", malformed)
    assert malformed.attributes["fleet.benchmark.model_id"] == "m-bench"


def test_config_loaded_program_destination_and_source_affect_identity(registry, monkeypatch):
    models, _ = registry
    values = [obs.associate_turn_agent(engine(), "config", "1", Span())]
    values.append(obs.associate_turn_agent(engine("changed"), "config", "1", Span()))
    values.append(obs.associate_turn_agent(engine(), "changed", "1", Span()))
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "another dirty snapshot"})
    values.append(obs.associate_turn_agent(engine(), "config", "1", Span()))
    assert len(set(values)) == len(models) == 4
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://another.invalid")
    obs.associate_turn_agent(engine(), "config", "1", Span())
    assert len(obs._AGENT_MODELS) == 5


@pytest.mark.usefixtures("registry")
def test_parallel_sessions_associate_only_their_current_trace(monkeypatch):
    current = ContextVar("trace")
    recorded = {}
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: recorded.update({current.get(): kwargs}))

    def run(index):
        current.set(index)
        model = obs.associate_turn_agent(engine(str(index)), "config", "1", Span())
        return index, model

    with ThreadPoolExecutor(max_workers=4) as pool:
        expected = dict(pool.map(run, range(8)))
    assert {index: value["model_id"] for index, value in recorded.items()} == expected
    assert len(set(expected.values())) == 8


@pytest.mark.usefixtures("registry")
def test_observation_failure_is_sanitized_and_does_not_raise(monkeypatch, caplog):
    def fail(**_kwargs):
        raise RuntimeError("credential-that-must-never-be-logged")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    span = Span()
    assert obs.associate_turn_agent(engine(), "config", "1", span) is None
    assert span.attributes["fleet.agent.identity_status"] == "unversioned"
    assert "credential-that-must-never-be-logged" not in caplog.text


@pytest.mark.usefixtures("registry")
def test_slow_lookup_does_not_block_other_cached_identities(monkeypatch):
    cached = obs.associate_turn_agent(engine("cached"), "config", "1", Span())
    entered, release = Event(), Event()
    original = obs.mlflow.search_logged_models

    def slow_search(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(obs.mlflow, "search_logged_models", slow_search)
    monkeypatch.setattr(obs, "_AGENT_LOOKUP_WAIT_SECONDS", 0.01)
    span = Span()
    try:
        assert obs.associate_turn_agent(engine("slow"), "config", "1", span) is None
        assert entered.is_set()
        assert span.attributes["fleet.agent.identity_status"] == "unversioned"
        assert obs.associate_turn_agent(engine("cached"), "config", "1", Span()) == cached
    finally:
        release.set()


def test_startup_snapshot_is_frozen_and_wheel_needs_no_git(tmp_path, monkeypatch):
    package = tmp_path / "wheel" / "fleet_rlm"
    package.mkdir(parents=True)
    (package / "engine.py").write_text("behavior = 1")
    (package / ".env").write_text("private configuration")
    (package / "memory.json").write_text("private memory")
    monkeypatch.setattr(obs.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()))
    first = obs.serving_snapshot(package)
    (package / ".env").write_text("rotated private configuration")
    assert obs.serving_snapshot(package) == first
    (package / "engine.py").write_text("behavior = 2")
    second = obs.serving_snapshot(package)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    assert "fleet.agent.git_commit" not in first
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", None)
    monkeypatch.setattr(obs, "serving_snapshot", lambda: first)
    obs.capture_serving_snapshot()
    monkeypatch.setattr(obs, "serving_snapshot", lambda: second)
    obs.capture_serving_snapshot()
    assert first == obs._STARTUP_IDENTITY


def test_bundled_resource_changes_are_included_but_optimizer_and_data_are_excluded(tmp_path):
    (tmp_path / "engine.py").write_text("pass")
    resource = tmp_path / "bundled_skills" / "example" / "SKILL.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("first instruction")
    first = obs.serving_snapshot(tmp_path)
    resource.write_text("second instruction")
    second = obs.serving_snapshot(tmp_path)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    (tmp_path / "optimize").mkdir()
    (tmp_path / "optimize" / "scorer.py").write_text("pass")
    (tmp_path / "dataset.csv").write_text("sensitive")
    assert obs.serving_snapshot(tmp_path) == second


def test_native_logged_model_search_reuse_and_persisted_trace_association(tmp_path):
    """Exercise pinned MLflow against a disposable local store in an isolated process."""
    import os
    import subprocess
    import sys

    program = """
import sys
from pathlib import Path
from types import SimpleNamespace
import mlflow
from fleet_rlm import observability as obs
root = Path(sys.argv[1])
mlflow.set_tracking_uri(f"sqlite:///{root / 'tracking.db'}")
experiment = mlflow.create_experiment("agent-contract", artifact_location=(root / "artifacts").as_uri())
mlflow.set_experiment(experiment_id=experiment)
obs._STARTUP_IDENTITY = {"fleet.agent.source_digest": "offline-source"}
benchmark_model = mlflow.create_external_model(name="benchmark", experiment_id=experiment, model_type="agent")
import os
os.environ["FLEET_MLFLOW_BENCHMARK_MODEL_ID"] = benchmark_model.model_id
os.environ["FLEET_MLFLOW_BENCHMARK_TAGS"] = '{"fleet.benchmark.campaign":"offline"}'
engine = SimpleNamespace(dump_state=lambda: {"predict": {"signature": "loaded-program", "lm": None}})
with mlflow.start_span(name="fleet_turn") as span:
    first = obs.associate_turn_agent(engine, "config", experiment, span)
    assert first is not None
    assert span.attributes["fleet.agent.model_id"] == first
    trace_id = span.trace_id
obs._AGENT_MODELS.clear()
with mlflow.start_span(name="fleet_turn") as span:
    assert obs.associate_turn_agent(engine, "config", experiment, span) == first
models = mlflow.search_logged_models(experiment_ids=[experiment], output_format="list")
assert {model.model_id for model in models} == {benchmark_model.model_id, first}
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
assert trace.info.trace_metadata["mlflow.modelId"] == benchmark_model.model_id
assert trace.info.tags["fleet.benchmark.campaign"] == "offline"
assert trace.data.spans[0].attributes["fleet.agent.model_id"] == first
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )
    assert result.returncode == 0, result.stderr


# Native usage accounting


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {"prompt_tokens": 12, "completion_tokens": 5},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
        ),
        ({"prompt_tokens": 0, "completion_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        ({"prompt_tokens": 12}, {"prompt_tokens": 12}),
        ({"completion_tokens": 5}, {"completion_tokens": 5}),
        ({}, {}),
    ],
    ids=["derive", "reported", "reported-zero", "derive-zero", "input-only", "output-only", "no-usage"],
)
def test_usage_summary_derives_only_from_complete_receipts(entry, expected):
    assert _usage_summary(entry) == expected


@pytest.fixture
def usage_span(monkeypatch):
    attributes = {}
    span = SimpleNamespace(span_type="LLM", set_attribute=lambda key, value: attributes.__setitem__(key, value))
    monkeypatch.setattr("fleet_rlm.observability.mlflow.get_current_active_span", lambda: span)
    return attributes


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_generation_usage_normalizes_once_before_native_rollup(usage_span, fails):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        for call_id, counts, expected_total in [("first", (12, 5), 17), ("second", (3, 4), 7)]:
            collector.on_lm_start(call_id, SimpleNamespace(model="model"), {})
            dspy.settings.usage_tracker.add_usage(
                "model", {"prompt_tokens": counts[0], "completion_tokens": counts[1], "provider_detail": "preserved"}
            )
            collector.on_lm_end(call_id, None, RuntimeError("failed") if fails else None)
            assert usage_span["mlflow.chat.tokenUsage"] == {
                "input_tokens": counts[0],
                "output_tokens": counts[1],
                "total_tokens": expected_total,
            }
            assert dspy.settings.usage_tracker is parent
            assert collector.finish_usage(call_id) == {}
    assert parent.get_total_tokens()["model"] == {
        "prompt_tokens": 15,
        "completion_tokens": 9,
        "total_tokens": 24,
        "provider_detail": "preserved",
    }
    assert len(parent.usage_data["model"]) == 2
    assert all(entry["provider_detail"] == "preserved" for entry in parent.usage_data["model"])
    assert collector._usage_scopes == {}


@pytest.mark.parametrize(
    "invalid", [None, True, -1, float("nan"), float("inf")], ids=["none", "bool", "negative", "nan", "inf"]
)
def test_generation_invalid_counts_stay_unknown_before_rollup(usage_span, invalid):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage(
            "model", {"prompt_tokens": invalid, "completion_tokens": 5, "total_tokens": invalid}
        )
        assert collector.finish_usage("call") == {"completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"completion_tokens": 5}
    assert parent.usage_data["model"] == [{"completion_tokens": 5}]


def test_generation_does_not_derive_total_from_complementary_partial_receipts(usage_span):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12})
        dspy.settings.usage_tracker.add_usage("model", {"completion_tokens": 5})
        assert collector.finish_usage("call") == {"prompt_tokens": 12, "completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"input_tokens": 12, "output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"prompt_tokens": 12, "completion_tokens": 5}
    assert len(parent.usage_data["model"]) == 2


def test_generation_restores_native_scope_when_span_annotation_fails(monkeypatch):
    def reject_attribute(*_args):
        raise RuntimeError("annotation failed")

    span = SimpleNamespace(span_type="LLM", set_attribute=reject_attribute)
    monkeypatch.setattr("fleet_rlm.observability.mlflow.get_current_active_span", lambda: span)
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12, "completion_tokens": 5})
        with pytest.raises(RuntimeError, match="annotation failed"):
            collector.finish_usage("call")
        assert dspy.settings.usage_tracker is parent
    assert parent.get_total_tokens()["model"]["total_tokens"] == 17
    assert len(parent.usage_data["model"]) == 1
    assert collector._usage_scopes == {}


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_should_restore_parent_usage_context_on_async_completion(fails):
    collector = LMUsageCallback()
    parent = UsageTracker()

    async def call():
        with dspy.context(usage_tracker=parent):
            collector.on_lm_start("call", SimpleNamespace(model="model"), {})
            await asyncio.sleep(0)
            dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 7, "total_tokens": 9})
            collector.on_lm_end("call", None, RuntimeError("failed") if fails else None)
            assert dspy.settings.usage_tracker is parent

    asyncio.run(call())
    assert parent.get_total_tokens()["model"]["total_tokens"] == 9
    assert collector._usage_scopes == {}
    assert usage_callbacks([object(), collector])[0] is collector


def test_should_ignore_decision_usage_and_omit_missing_generation_usage():
    collector = LMUsageCallback()
    with dspy.context(usage_tracker=None):
        collector.on_lm_start("decision", SimpleNamespace(supports_decision_requests=True), {})
        assert collector.finish_usage("decision") == {}
        collector.on_lm_start("generation", SimpleNamespace(model="model"), {})
        assert collector.finish_usage("generation") == {}
        assert dspy.settings.usage_tracker is None


@pytest.mark.parametrize("fails", [False, True])
def test_typesafe_partial_usage_rolls_up_once_even_after_failure(fails):
    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent):
        try:
            with decision_usage(span):
                dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 883, "completion_tokens": 39})
                if fails:
                    raise RuntimeError("decision failed after usage was reported")
        except RuntimeError:
            assert fails
        assert dspy.settings.usage_tracker is parent
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 883, "output_tokens": 39, "total_tokens": 922}
    assert parent.get_total_tokens()["jev"] == {"prompt_tokens": 883, "completion_tokens": 39, "total_tokens": 922}


def test_decision_usage_keeps_unknown_counts_unknown():
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with decision_usage(span):
        dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 7, "completion_tokens": None})
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 7}
    attributes.clear()
    with decision_usage(span):
        pass
    assert attributes == {}


def test_batched_decision_workers_share_one_usage_receipt():
    from dspy.utils.callback import with_callbacks

    class DecisionLM(dspy.BaseLM):
        supports_decision_requests = True

        def __init__(self):
            super().__init__(model="typesafe:jev")

        @with_callbacks
        def __call__(self, count):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": count, "completion_tokens": 1})
            return dspy.Prediction(answer="relevant")

    class Decision(dspy.Module):
        def forward(self, count):
            return DecisionLM()(count)

    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent), decision_usage(span):
        results = Decision().batch([dspy.Example(count=n).with_inputs("count") for n in (2, 3, 4)], num_threads=3)
    assert len(results) == 3
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12}
    assert parent.get_total_tokens()["jev"]["total_tokens"] == 12


def test_should_capture_per_call_and_aggregate_tokens_through_production_tracing(tmp_path):
    # A subprocess isolates global DSPy/MLflow callbacks. The only backend is temporary SQLite.
    missing_totals = False
    code = r"""
import json
import dspy
import mlflow
from dspy.lm15 import Message, Response, Usage
from fleet_rlm.observability import configure_mlflow_observability
import fleet_rlm.engine.decisions as decisions
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.contracts import Route
from fleet_rlm.observability import decision_usage, usage_callbacks
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

def refuse_sandbox(*_args, **_kwargs):
    raise AssertionError("Offline tracing test must not provision a sandbox")
DaytonaSandboxManager._ensure_sandbox = refuse_sandbox

class Engine:
    def __init__(self):
        self.count = 0
        self.responses = iter([
            "<plan>checked</plan>\n<answer>5</answer>",
            "invalid output",
            '{"answer": "5"}',
        ])
    def complete(self, request):
        self.count += 1
        inputs, outputs = self.count * 10, self.count + 3
        return Response(id=None, model="offline", message=Message.assistant(next(self.responses)), finish_reason="stop",
                        usage=Usage(input_tokens=inputs, output_tokens=outputs, total_tokens=inputs+outputs))
    def close(self):
        pass

configure_mlflow_observability("usage-regression", __import__('os').environ['MLFLOW_TRACKING_URI'])
lm = dspy.LM("openai/offline", engine=Engine(), cache=False)
if __import__('os').environ['MISSING_TOTALS'] == '1':
    # The typed engine derives totals; simulate partial provider receipts at native ingestion.
    from dspy.utils.usage_tracker import UsageTracker
    add_usage = UsageTracker.add_usage
    def without_total(tracker, model, entry):
        if model == 'openai/offline':
            entry = {key: value for key, value in entry.items() if key != 'total_tokens'}
        return add_usage(tracker, model, entry)
    UsageTracker.add_usage = without_total
manager = DaytonaSandboxManager("key", "org", "volume", "offline")
engine = FleetRuntime(manager, lm, lm)
def choose(**_kwargs):
    with mlflow.start_span(name="typesafe_route_selection", span_type="LLM") as span:
        with decision_usage(span):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens":883,"completion_tokens":39})
    return Route.DIRECT
decisions.select_route = choose
with mlflow.start_span(name="fleet_turn") as root:
    with dspy.context(disable_history=True, track_usage=True):
        result = engine(workspace_manifest="", memory_summary="", task="return five")
    assert result.answer == "5" and result.lm_calls == 1
    with dspy.context(lm=lm, callbacks=usage_callbacks(list(dspy.settings.callbacks)),
                      adapter=dspy.ChatAdapter(), disable_history=True, track_usage=True):
        repaired = dspy.Predict("task -> answer")(task="return five")
    assert repaired.answer == "5"
    trace_id = root.trace_id
assert lm.history == []
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
calls = [s for s in trace.data.spans if s.name == 'LM.__call__']
assert len(calls) == 3, [(s.name,s.attributes) for s in trace.data.spans]
assert [s.attributes['mlflow.chat.tokenUsage'] for s in calls] == [
    {'input_tokens':10,'output_tokens':4,'total_tokens':14},
    {'input_tokens':20,'output_tokens':5,'total_tokens':25},
    {'input_tokens':30,'output_tokens':6,'total_tokens':36},
]
assert json.loads(trace.info.trace_metadata['mlflow.trace.tokenUsage']) == {
    'input_tokens':943,'output_tokens':54,'total_tokens':997,
}
print(f"Verified offline trace {trace_id}: {len(trace.data.spans)} spans, 3 model calls, 997 tokens")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'usage.db'}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MISSING_TOTALS": str(int(missing_totals)),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 model calls, 997 tokens" in result.stdout


def test_turn_callbacks_compose_ambient_callbacks_with_one_usage_collector():
    import dspy

    from fleet_rlm.observability import LMUsageCallback, ambient_callbacks, turn_callbacks

    marker = object()
    with dspy.context(callbacks=[marker]):
        composed = turn_callbacks()
        assert marker in ambient_callbacks()
        assert sum(isinstance(item, LMUsageCallback) for item in composed) == 1
        assert len(composed) == 2
        # dspy.context replaces the callback list, so re-spreading the ambient list is mandatory.
        assert marker in composed


def test_typesafe_callbacks_drop_the_mlflow_dspy_hook():
    import dspy
    from mlflow.dspy.callback import MlflowCallback

    from fleet_rlm.observability import typesafe_callbacks

    marker = object()
    with dspy.context(callbacks=[MlflowCallback(), marker]):
        assert typesafe_callbacks() == [marker]


def test_stream_callbacks_append_without_reordering():
    import dspy

    from fleet_rlm.observability import stream_callbacks

    marker, collector = object(), object()
    with dspy.context(callbacks=[marker]):
        # The Turn's stream collector must stay last: it is the API's terminal-event owner.
        assert stream_callbacks(collector) == [marker, collector]


# Redaction and tracing


@pytest.mark.parametrize("destination", [None, "", "catalog.schema.prefix"])
def test_databricks_trace_experiment_preserves_native_activation(monkeypatch, destination):
    from fleet_rlm import observability

    experiment = (
        None
        if destination is None
        else SimpleNamespace(tags={"mlflow.experiment.databricksTraceDestinationPath": destination})
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    calls = []
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda name, **kwargs: calls.append((name, kwargs)) or experiment
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix="prefix",
            tracing_sql_warehouse_id="warehouse",
        )
        is experiment
    )
    assert len(calls) == 1
    assert calls[0][0] == "/Shared/runtime"
    assert calls[0][1]["trace_location"].full_table_prefix == "catalog.schema.prefix"


@pytest.mark.parametrize("prefix", ["requested", ""])
def test_databricks_trace_destination_mismatch_prevents_activation(monkeypatch, prefix):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.existing"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda *_args, **_kwargs: pytest.fail("must not activate")
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    with pytest.raises(observability.MlflowTraceDestinationMismatchError, match="trace destination mismatch"):
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix=prefix,
        )


def test_databricks_trace_experiment_omitted_prefix_uses_native_experiment_id(monkeypatch):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.17"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(observability.mlflow, "set_experiment", lambda *_args, **_kwargs: experiment)
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime", databricks=True, trace_catalog="catalog", trace_schema="schema"
        )
        is experiment
    )


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.observability as memory_and_eval

    calls: dict[str, Any] = {}
    monkeypatch.setattr(memory_and_eval.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("uri", uri))
    monkeypatch.setattr(
        memory_and_eval.mlflow.tracing,
        "configure",
        lambda **kwargs: calls.setdefault("processors", kwargs),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "set_experiment",
        lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "dspy",
        SimpleNamespace(autolog=lambda **kwargs: calls.setdefault("autolog", kwargs)),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(experiment_id="experiment-id"),
    )

    experiment_id = memory_and_eval.configure_mlflow_observability(
        "fleet-rlm-test",
        "sqlite:///mlflow-test.db",
    )

    assert experiment_id == "experiment-id"
    assert calls == {
        "uri": "sqlite:///mlflow-test.db",
        "experiment": "fleet-rlm-test",
        "processors": {"span_processors": [memory_and_eval.sanitize_trace_span]},
        "autolog": {
            "log_traces": True,
            "log_traces_from_compile": False,
            "log_traces_from_eval": False,
        },
    }


def test_trace_span_sanitization_redacts_secret_fields_and_bounds_content():
    import fleet_rlm.observability as memory_and_eval

    class Span:
        def __init__(self):
            self.inputs = {"token": "secret-value", "task": "Bearer abcdefghijklmnopqrstuvwxyz"}
            self.outputs = {"answer": "x" * (memory_and_eval._TRACE_STRING_LIMIT + 1)}

        def set_inputs(self, value):
            self.inputs = value

        def set_outputs(self, value):
            self.outputs = value

    span = Span()
    memory_and_eval.sanitize_trace_span(span)
    assert span.inputs["token"] == "[redacted]"
    assert "[redacted]" in span.inputs["task"]
    assert len(span.outputs["answer"]) < memory_and_eval._TRACE_STRING_LIMIT + 100


def test_native_required_skill_arguments_do_not_export_resource_bytes():
    from fleet_rlm.observability import trace_value
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: source\ndescription: Source\n---\nInstructions"
    skill = captured_required_skill("source", "workspace", (("SKILL.md", raw), ("secret.dat", b"private-resource")))
    exported = trace_value({"required_skills": (skill,)}, nodes=[0])
    assert exported == {"required_skills": ["[RequiredSkill omitted]"]}
    assert "private-resource" not in str(exported)


def test_trace_shows_reasoning_text_but_streams_still_redact_it() -> None:
    import fleet_rlm.observability as memory_and_eval

    cleaned = memory_and_eval.trace_value(
        {"reasoning_content": "Plan: use api_key=hidden", "api_key": "value"},
        nodes=[0],
    )
    assert cleaned == {"reasoning_content": "Plan: use api_key=[redacted]", "api_key": "[redacted]"}
    assert '"reasoning_content": "[redacted]"' in memory_and_eval.activity_preview({"reasoning_content": "private"})


def test_typesafe_route_filters_the_incompatible_mlflow_callback() -> None:
    from mlflow.dspy.callback import MlflowCallback

    from fleet_rlm.observability import without_mlflow_dspy_callback

    sse_callback = object()
    # Matching is by class identity, not by module/name strings, so a look-alike from elsewhere survives.
    impostor = type("MlflowCallback", (), {"__module__": "mlflow.dspy.callback"})()

    assert without_mlflow_dspy_callback([MlflowCallback(), sse_callback]) == [sse_callback]
    assert without_mlflow_dspy_callback([impostor, sse_callback]) == [impostor, sse_callback]


def test_snapshot_provenance_is_written_to_the_turn_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.engine.turn as turn_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, SNAPSHOT_NAME, clear_preflight_cache, preflight

    clear_preflight_cache()
    provider = SimpleNamespace(
        get=lambda name: SimpleNamespace(name=name, id="snap-1", state="active", cpu=4, mem=8, disk=8)
    )
    identity = preflight(provider, name=SNAPSHOT_NAME, api_key="key", org_id="org")
    expected = {
        "snapshot_name": SNAPSHOT_NAME,
        "snapshot_id": "snap-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": DEFAULT_DEFINITION.digest(),
    }
    captured: dict[str, Any] = {}

    class Span:
        def set_inputs(self, value: dict[str, Any]) -> None:
            captured["inputs"] = value

        def set_attributes(self, value: dict[str, Any]) -> None:
            captured["attributes"] = value

    monkeypatch.setattr(turn_module.mlflow, "update_current_trace", lambda **_kwargs: None)
    runtime = SimpleNamespace(
        configuration_overrides={},
        engine=SimpleNamespace(execution_mode="hybrid", root_lm=None, sub_lm=None),
        sandbox_mgr=SimpleNamespace(
            turn_timeout_seconds=900,
            snapshot_provenance=identity.provenance,
            deadline_provenance=lambda: {"deadline_source": "configuration", "effective_seconds": 900},
        ),
    )

    turn_module._describe_turn(
        Span(),
        runtime,
        FleetSettings(_env_file=None),
        "session-a",
        "compute a digit",
        "turn-1",
        None,
        {"manifest": "/workspace/files (empty)", "context_files": {}},
        None,
    )

    attributes = captured["attributes"]
    assert {key: attributes[key] for key in expected} == expected
    assert attributes["deadline_source"] == "configuration"
    assert attributes["effective_seconds"] == 900
    assert "definition_digest" not in attributes
