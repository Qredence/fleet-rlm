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
