"""Offline coverage for API-owned experiment lifecycle and routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.app import create_app
from fleet_rlm.config import FleetSettings
from fleet_rlm.experiments import (
    _RECOVERY_INTERVAL_SECONDS,
    ExperimentBusyError,
    ExperimentManager,
    _require_worker_pipes,
)
from fleet_rlm.optimization_runs import ExperimentOptions


def test_worker_pipe_validation_is_always_on():
    process = SimpleNamespace(stdin=None, stdout=object(), stderr=object())
    with pytest.raises(RuntimeError, match="pipes were not created"):
        _require_worker_pipes(process)


def test_failed_approval_exposes_sanitized_failure_and_success_clears_it(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": "approving", "candidate_version": "s1-run"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        manifest = {
            "version": "s1-run",
            "digest": hashlib.sha256(b"candidate").hexdigest(),
            "dependencies": {"incumbent_digest": hashlib.sha256(b"incumbent").hexdigest()},
            "gate": {"passed": True},
        }
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        active = tmp_path / "fleet.json"
        active.write_bytes(b"incumbent")
        failure = {"type": "ValueError", "detail": "Approval failed; exception details omitted."}
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "failed", "failure": failure}})
        await manager._finish("s1-run", SimpleNamespace(returncode=1), None, approval=True)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "completed" and record["failure"] == failure
        active.write_bytes(b"candidate")
        store.update_optimization_run("s1-run", {"status": "approving", "worker_outcome": {"status": "approved"}})
        await manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=True)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "approved" and record["failure"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("manifest", [[], {"version": "s1-run", "gate": None}, {"version": "s1-run", "digest": ""}])
def test_invalid_approval_manifest_becomes_interrupted_without_install(tmp_path, manifest):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": "approving"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        await manager._recover_approval(store.get_optimization_run("s1-run"))
        assert store.get_optimization_run("s1-run")["status"] == "interrupted"
        assert not (tmp_path / "fleet.json").exists()

    asyncio.run(scenario())


def test_output_drain_never_writes_progress_even_when_storage_is_unavailable(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("private database failure")

        store.update_optimization_run = unavailable
        stream = asyncio.StreamReader()
        stream.feed_data(b'FLEET_EVENT {"type":"progress","run_id":"s1-run","metric_calls_used":1}\n')
        stream.feed_data(b"x" * 90_000 + b"\nordinary output\n")
        stream.feed_eof()
        await asyncio.wait_for(manager._discard_output(stream), timeout=2)
        assert stream.at_eof()
        assert not (tmp_path / "runs" / "s1-run" / "gepa.stop").exists()

    asyncio.run(scenario())


class MemoryStore:
    """Small ledger fake with the same ownership/status fences as Store."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._lease = threading.Lock()
        self._owners: dict[str, threading.Lock] = {}

    def acquire_optimization_owner_lease(self, owner_id):
        with self._lock:
            lock = self._owners.setdefault(owner_id, threading.Lock())
            if not lock.acquire(blocking=False):
                raise RuntimeError("API owner lease already held")
            return _Lease(lock)

    def optimization_owner_alive(self, owner_id):
        with self._lock:
            return owner_id in self._owners and self._owners[owner_id].locked()

    def acquire_optimization_lease(self):
        if not self._lease.acquire(blocking=False):
            raise RuntimeError("An optimization run is already owned")
        return _Lease(self._lease)

    def create_optimization_run(
        self,
        run_id: str,
        request_id: str,
        options: dict[str, Any],
        owner_id: str,
        *,
        kind: str = "optimize",
        managed: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if any(row["status"] in {"running", "approving"} for row in self.rows.values()):
                raise ValueError("An optimization run is already active")
            if any(row["request_id"] == request_id for row in self.rows.values()):
                raise ValueError("optimization request already exists")
            now = datetime.now(UTC).isoformat()
            record = {
                "run_id": run_id,
                "request_id": request_id,
                "kind": kind,
                "options": dict(options),
                "owner_id": owner_id,
                "managed": managed,
                "status": "running",
                "stage": "preparing",
                "stop_requested": False,
                "created_at": now,
                "updated_at": now,
            }
            self.rows[run_id] = record
            return dict(record)

    def get_optimization_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get(run_id)
            return dict(row) if row else None

    def list_optimization_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.rows.values()]

    def update_optimization_run(
        self,
        run_id: str,
        updates: dict[str, Any],
        *,
        owner_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or (owner_id is not None and row["owner_id"] != owner_id):
                return None
            if expected_statuses is not None and row["status"] not in expected_statuses:
                return None
            status = updates.get("status", row["status"])
            if status in {"running", "approving"} and any(
                other is not row and other["status"] in {"running", "approving"} for other in self.rows.values()
            ):
                raise ValueError("Another optimization run is active")
            row.update(updates)
            row["updated_at"] = datetime.now(UTC).isoformat()
            return dict(row)

    def request_optimization_stop(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None:
                raise ValueError("optimization run was not found")
            if row["status"] != "running":
                raise ValueError("only a running optimization can be stopped")
            row["stop_requested"] = True
            return dict(row)

    def claim_optimization_review(self, run_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or row["status"] not in {"completed", "approving"}:
                raise ValueError("only a completed or recoverable approving run can be reviewed")
            if any(other is not row and other["status"] in {"running", "approving"} for other in self.rows.values()):
                raise ValueError("Another optimization run is active")
            row.update(status="approving", owner_id=owner_id)
            return dict(row)

    def reject_optimization_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or row["status"] not in {"completed", "rejected"}:
                raise ValueError("only a completed optimization run can be rejected")
            row.update(status="rejected", decision="rejected")
            return dict(row)

    def import_optimization_run(self, record: dict[str, Any]) -> bool:
        with self._lock:
            if record["run_id"] in self.rows:
                return False
            if record.get("status") in {"running", "approving"} and any(
                row["status"] in {"running", "approving"} for row in self.rows.values()
            ):
                raise ValueError("Another optimization run is active")
            self.rows[record["run_id"]] = {"kind": "optimize", **record}
            return True


class _Lease:
    def __init__(self, lock: threading.Lock) -> None:
        self.lock = lock

    def close(self) -> None:
        if self.lock.locked():
            self.lock.release()


def _settings(root: Path) -> FleetSettings:
    return FleetSettings(_env_file=None, compiled_program_dir=str(root))


def _manager_with_store(root: Path) -> tuple[MemoryStore, ExperimentManager]:
    store = MemoryStore()
    return store, ExperimentManager(_settings(root), store)  # type: ignore[arg-type]


def _app_client(root: Path, store: MemoryStore) -> tuple[Any, TestClient]:
    app = create_app(
        settings=_settings(root),
        initialize_db=False,
        configure_observability=False,
        store=store,  # type: ignore[arg-type]
    )
    return app, TestClient(app, raise_server_exceptions=False)


def _optimization_run(run_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "request_id": f"request-{run_id}",
        "options": {},
        "owner_id": "old-api-owner",
        "managed": True,
        "status": "running",
        "stage": "search",
        "stop_requested": False,
        **overrides,
    }


def _completed(store: MemoryStore, run_id: str, *, gate: bool = True, **overrides: Any) -> bool:
    now = datetime.now(UTC).isoformat()
    return store.import_optimization_run(
        {
            "run_id": run_id,
            "request_id": f"request-{run_id}",
            "options": {},
            "owner_id": "imported",
            "managed": False,
            "status": "completed",
            "stage": "completed",
            "created_at": now,
            "updated_at": now,
            "candidate_version": run_id,
            "gate": {"passed": gate, "reasons": []},
            **overrides,
        }
    )


def test_api_owned_run_is_idempotent_global_and_drain_fences_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = kind, options, approval
            started.set()
            await finish.wait()
            store.update_optimization_run(
                run_id,
                {"status": "approved" if approval else "completed", "stage": "completed"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",) if approval else ("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        options = ExperimentOptions(max_metric_calls=8)
        first = await manager.start("request-1", options)
        await started.wait()
        retry = await manager.start("request-1", options)
        assert retry["run_id"] == first["run_id"]
        with pytest.raises(ExperimentBusyError):
            await manager.start("request-2", options)

        draining = await manager.begin_drain()
        assert draining["accepting"] is False and draining["busy"] is True
        with pytest.raises(ExperimentBusyError):
            await manager.start("request-3", options)
        finish.set()
        await manager.close()
        final = await manager.get(first["run_id"])
        assert final["status"] == "completed"
        assert manager.busy is False

    asyncio.run(scenario())


def test_api_owned_cancel_persists_stop_intent_and_cooperatively_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = kind, options, approval
            started.set()
            while not (store.get_optimization_run(run_id) or {}).get("stop_requested"):
                await asyncio.sleep(0.01)
            stopped.set()
            store.update_optimization_run(
                run_id,
                {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
                owner_id=manager.owner_id,
                expected_statuses=("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.start("cancel-request", ExperimentOptions())
        await started.wait()
        accepted = await manager.cancel(record["run_id"])
        assert accepted["stop_requested"] is True
        assert (tmp_path / "runs" / record["run_id"] / "gepa.stop").is_file()
        await stopped.wait()
        await manager._task
        assert (await manager.get(record["run_id"]))["status"] == "cancelled"
        await manager.close()

    asyncio.run(scenario())


def test_review_retry_reuses_claim_and_manager_close_waits_for_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        _completed(store, "review-candidate")
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            assert approval is True
            _ = kind, options
            started.set()
            await finish.wait()
            store.update_optimization_run(
                run_id,
                {"status": "approved", "stage": "approved", "decision": "approved"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.review("review-candidate", "approved", "review-key")
        await started.wait()
        retry = await manager.review("review-candidate", "approved", "review-key")
        assert retry["status"] == "approving"
        assert retry["review_request_id"] == "review-key"
        with pytest.raises(ExperimentBusyError):
            await manager.review("review-candidate", "rejected", "other-key")

        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert not closing.done()
        finish.set()
        await closing
        assert (await manager.get(record["run_id"]))["status"] == "approved"

    asyncio.run(scenario())


def test_initialize_fences_abandoned_managed_worker_as_interrupted(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        run_id = "recovery-run"
        store.import_optimization_run(_optimization_run(run_id, request_id="old-request"))
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        record = store.get_optimization_run(run_id)
        assert record["status"] == "interrupted"
        assert record["stop_requested"] is False
        assert record["finished_at"]
        assert record["candidate_version"] is None
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_started", [False, True])
@pytest.mark.parametrize("status", ["running", "approving"])
def test_second_api_preserves_live_foreign_owner_including_pending_launch(tmp_path, worker_started, status):
    async def scenario():
        store, owner = _manager_with_store(tmp_path)
        await owner.initialize()
        record = store.create_optimization_run("s1-run", "request", {}, owner.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": status})
        worker = store.acquire_optimization_lease() if worker_started else None
        observer = ExperimentManager(_settings(tmp_path), store)
        try:
            await observer.initialize()
            await observer.list()
            current = store.get_optimization_run(record["run_id"])
            assert current["status"] == status
            assert current["stop_requested"] is False
            assert not (tmp_path / "runs" / record["run_id"] / "gepa.stop").exists()
        finally:
            if worker:
                worker.close()
            await observer.close()
            await owner.close()
        assert not store.optimization_owner_alive(owner.owner_id)

    asyncio.run(scenario())


def test_owner_lease_survives_drain_until_worker_joined(tmp_path):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)
        await manager.initialize()
        finish = asyncio.Event()
        manager._task = asyncio.create_task(finish.wait())
        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert manager.accepting is False
        assert store.optimization_owner_alive(manager.owner_id)
        finish.set()
        await closing
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


@pytest.mark.parametrize("approval", [False, True])
def test_manager_recovers_own_finished_worker_after_final_write_failure(tmp_path, monkeypatch, approval):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)
        await manager.initialize()
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run(
            "s1-run",
            {"status": "approving" if approval else "running", "worker_outcome": {"status": "completed"}},
        )
        if approval:
            directory = tmp_path / "candidates"
            directory.mkdir()
            (directory / "s1-run.manifest.json").write_text(
                json.dumps(
                    {
                        "version": "s1-run",
                        "digest": hashlib.sha256(b"candidate").hexdigest(),
                        "gate": {"passed": True},
                        "dependencies": {},
                    }
                )
            )
            (tmp_path / "fleet.json").write_bytes(b"candidate")
        update = store.update_optimization_run

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("final write unavailable")

        monkeypatch.setattr(store, "update_optimization_run", unavailable)
        manager._task = asyncio.create_task(
            manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=approval)
        )
        with pytest.raises(RuntimeError, match="final write unavailable"):
            await manager._task
        assert not manager.busy and store.optimization_owner_alive(manager.owner_id)
        monkeypatch.setattr(store, "update_optimization_run", update)
        await manager.list()
        record = store.get_optimization_run("s1-run")
        assert record["status"] == ("approved" if approval else "interrupted")
        if not approval:
            assert record["candidate_version"] is None
        # close() still propagates the original task failure after releasing its lease.
        with pytest.raises(RuntimeError, match="final write unavailable"):
            await manager.close()
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


def test_initialization_failure_releases_owner_lease_on_cleanup(tmp_path, monkeypatch):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)

        def fail(*_args, **_kwargs):
            raise RuntimeError("history unavailable")

        # initialize() reconciles unthrottled, so the failure is injected on the shared body.
        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", fail)
        with pytest.raises(RuntimeError, match="history unavailable"):
            await manager.initialize()
        assert store.optimization_owner_alive(manager.owner_id)
        await manager.close()
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


def test_recovery_keeps_workers_active_until_global_lease_is_released(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        managed = _optimization_run("managed-orphan-run", request_id="managed-request")
        standalone = _optimization_run(
            "cli-orphan-run",
            request_id="cli-request",
            owner_id="cli-owner",
            managed=False,
        )
        store.import_optimization_run(managed)
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        assert store.get_optimization_run(managed["run_id"])["status"] == "running"
        assert store.get_optimization_run(managed["run_id"])["stop_requested"] is True
        assert (tmp_path / "runs" / managed["run_id"] / "gepa.stop").is_file()
        assert store.get_optimization_run(managed["run_id"])["status"] == "running"
        store._lease.release()
        await manager.list()
        assert store.get_optimization_run(managed["run_id"])["status"] == "interrupted"
        await manager.close()

        cli_store = MemoryStore()
        cli_store.import_optimization_run(standalone)
        cli_store._lease.acquire()
        cli_manager = ExperimentManager(_settings(tmp_path), cli_store)  # type: ignore[arg-type]
        await cli_manager.initialize()
        assert cli_store.get_optimization_run(standalone["run_id"])["status"] == "running"
        cli_store._lease.release()
        snapshot = await cli_manager.list()
        assert snapshot["jobs"][0]["status"] == "interrupted"
        await cli_manager.close()

    asyncio.run(scenario())


def test_same_approval_retry_reconciles_after_prior_owner_exits(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        run_id = "retry-recovery-candidate"
        installed = b"approved candidate"
        digest = hashlib.sha256(installed).hexdigest()
        candidates = tmp_path / "candidates"
        candidates.mkdir()
        (candidates / f"{run_id}.manifest.json").write_text(
            json.dumps(
                {
                    "version": run_id,
                    "digest": digest,
                    "gate": {"passed": True},
                    "dependencies": {"incumbent_digest": "old-incumbent"},
                }
            )
        )
        (tmp_path / "fleet.json").write_bytes(installed)
        store.import_optimization_run(
            _optimization_run(
                run_id,
                request_id="candidate-request",
                owner_id="old-review-owner",
                status="approving",
                stage="approving",
                review_request_id="approve-once",
                review_decision="approved",
            )
        )
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        with pytest.raises(ExperimentBusyError):
            await manager.review(run_id, "approved", "approve-once")
        assert store.get_optimization_run(run_id)["status"] == "approving"
        store._lease.release()
        result = await manager.review(run_id, "approved", "approve-once")
        assert result["status"] == "approved"
        assert manager._task is None
        await manager.close()

    asyncio.run(scenario())


def test_interrupted_run_keeps_its_saved_comparison_when_terminal_candidate_version_is_cleared(
    tmp_path: Path,
) -> None:
    run_id = "interrupted-evidence-partial"
    store = MemoryStore()
    store.import_optimization_run(
        {
            "run_id": run_id,
            "request_id": "partial-request",
            "options": {},
            "owner_id": "imported",
            "managed": False,
            "status": "interrupted",
            "stage": "interrupted",
            "candidate_version": None,
        }
    )
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"report":"partial diagnostics"}')
    manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
    detail = asyncio.run(manager.detail(run_id))
    assert detail["comparison"] == {"report": "partial diagnostics"}


def test_managed_subprocess_drains_noisy_output_without_replaying_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path), databricks_token="secret-token")
        manager = ExperimentManager(settings, store)  # type: ignore[arg-type]
        command: list[str] = []
        captured: dict[str, Any] = {}
        run_id_holder: dict[str, str] = {}

        class FakeStdin:
            def write(self, data: bytes) -> None:
                captured["stdin"] = data

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                captured["stdin_closed"] = True

        class FakeProcess:
            stdin = FakeStdin()

            def __init__(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = 0

            async def wait(self) -> int:
                run_id = run_id_holder["run_id"]
                row = store.get_optimization_run(run_id)
                store.update_optimization_run(
                    run_id,
                    {"worker_outcome": {"status": "completed", "candidate_version": run_id}},
                    owner_id=row["owner_id"],
                    expected_statuses=("running",),
                )
                return self.returncode

        async def fake_spawn(*args: str, **kwargs: Any) -> FakeProcess:
            command.extend(args)
            captured["spawn_kwargs"] = kwargs
            run_id = args[args.index("--managed-run-id") + 1]
            run_id_holder["run_id"] = run_id
            from fleet_rlm.optimize.gepa import GEPAProgressReporter

            reporter = GEPAProgressReporter(run_id, store=store, owner_id=manager.owner_id, emit_events=False)
            reporter.on_budget_updated({"metric_calls_used": 20, "iteration": 2})
            assert store.get_optimization_run(run_id)["progress"]["metric_calls_used"] == 20
            stdout = asyncio.StreamReader()
            stdout.feed_data(
                b"x" * 90_000
                + b"\n"
                + (
                    f'FLEET_EVENT {{"type":"progress","run_id":"{run_id}","stage":"search",'
                    '"metric_calls_used":10,"prompt":"must not persist"}\n'
                    f'FLEET_EVENT {{"type":"stage","run_id":"{run_id}","stage":"validation"}}\n'
                    f'FLEET_EVENT {{"type":"terminal","run_id":"{run_id}","status":"completed"}}\n'
                ).encode()
            )
            stdout.feed_eof()
            stderr = asyncio.StreamReader()
            stderr.feed_data(b"Authorization: Bearer secret-token\n")
            stderr.feed_eof()
            return FakeProcess(stdout, stderr)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        real_command = manager._command

        def capture_command(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> list[str]:
            run_id_holder["run_id"] = run_id
            result = real_command(run_id, kind, options, approval=approval)
            result.insert(result.index("--structured-events"), "--synthetic-output")
            return result

        manager._command = capture_command  # type: ignore[method-assign]
        await manager.start("worker-request", ExperimentOptions())
        await manager._task
        record = await manager.get(run_id_holder["run_id"])
        assert record["status"] == "completed"
        assert record["progress"] == {"metric_calls_used": 20, "iteration": 2}
        assert captured["stdin_closed"] is True
        assert b"secret-token" in captured["stdin"]
        assert "secret-token" not in command
        assert "--managed-owner-id" in command
        assert captured["spawn_kwargs"]["stdin"] is asyncio.subprocess.PIPE
        assert captured["spawn_kwargs"]["start_new_session"] is True

    asyncio.run(scenario())


def test_terminal_event_does_not_release_global_slot_until_worker_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        spawned = threading.Event()
        allow_exit = threading.Event()
        run_id: dict[str, str] = {}

        class FakeStdin:
            def write(self, _data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

        class FakeProcess:
            stdin = FakeStdin()
            returncode = 0

            def __init__(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
                self.stdout = stdout
                self.stderr = stderr

            async def wait(self) -> int:
                await asyncio.to_thread(allow_exit.wait)
                row = store.get_optimization_run(run_id["value"])
                store.update_optimization_run(
                    run_id["value"],
                    {"worker_outcome": {"status": "completed", "candidate_version": run_id["value"]}},
                    owner_id=row["owner_id"],
                    expected_statuses=("running",),
                )
                return 0

        async def fake_spawn(*args: str, **_kwargs: Any) -> FakeProcess:
            run_id["value"] = args[args.index("--managed-run-id") + 1]
            stdout = asyncio.StreamReader()
            stdout.feed_data(
                (
                    f'FLEET_EVENT {{"type":"progress","run_id":"{run_id["value"]}","stage":"search",'
                    '"metric_calls_used":1}\n'
                    f'FLEET_EVENT {{"type":"terminal","run_id":"{run_id["value"]}","status":"completed"}}\n'
                ).encode()
            )
            stdout.feed_eof()
            stderr = asyncio.StreamReader()
            stderr.feed_eof()
            spawned.set()
            return FakeProcess(stdout, stderr)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        record = await manager.start("reap-request", ExperimentOptions())
        assert await asyncio.to_thread(spawned.wait, 2)
        # Output has no authority while the managed child is still running.
        await asyncio.sleep(0.01)
        current = store.get_optimization_run(record["run_id"])
        assert "progress" not in current
        assert current["status"] == "running"
        with pytest.raises(ExperimentBusyError):
            await manager.start("second", ExperimentOptions())

        allow_exit.set()
        await manager._task
        assert (await manager.get(record["run_id"]))["status"] == "completed"
        await manager.close()

    asyncio.run(scenario())


def test_experiment_routes_keep_run_after_response_and_cancel_only_by_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    started = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, run_id: str, kind: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = kind, options
        assert not approval
        started.set()
        deadline = asyncio.get_running_loop().time() + 5
        while (
            not (manager.store.get_optimization_run(run_id) or {}).get("stop_requested")
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            run_id,
            {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
            owner_id=manager.owner_id,
            expected_statuses=("running",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    app, client = _app_client(tmp_path, store)
    with client:
        schema = app.openapi()
        operations = schema["paths"]
        assert operations["/improve/jobs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/JobList"
        }
        for path, method in (
            ("/improve/jobs", "post"),
            ("/improve/jobs/{run_id}/cancel", "post"),
            ("/improve/jobs/{run_id}/review", "post"),
        ):
            assert operations[path][method]["responses"]["202"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/JobEnvelope"
            }
        assert operations["/improve/jobs/{run_id}"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/JobDetail"}
        for path, method in (
            ("/improve/jobs", "get"),
            ("/improve/jobs", "post"),
            ("/improve/jobs/{run_id}", "get"),
            ("/improve/jobs/{run_id}/cancel", "post"),
            ("/improve/jobs/{run_id}/review", "post"),
        ):
            responses = operations[path][method]["responses"]
            for status in ("404", "409", "422", "503"):
                assert status in responses
                assert (
                    responses[status]["content"]["application/json"]["schema"]["$ref"]
                    == "#/components/schemas/ApiError"
                )
        request = {"request_id": "run_once", "max_metric_calls": 9}
        response = client.post("/improve/jobs", json=request)
        assert response.status_code == 202, response.text
        record = response.json()["job"]
        run_id = record["run_id"]
        assert record["status"] == "running"
        assert not {"owner_id", "managed", "worker_settings", "pid"}.intersection(record)
        assert started.wait(timeout=2)

        repeated = client.post("/improve/jobs", json=request)
        assert repeated.status_code == 202
        assert repeated.json()["job"]["run_id"] == run_id
        conflict = client.post("/improve/jobs", json={"request_id": "other", "max_metric_calls": 3})
        assert conflict.status_code == 409
        invalid = client.post("/improve/jobs", json={"request_id": "bad", "unknown": "field"})
        assert invalid.status_code == 422

        # The response/request connection has ended, but the detached manager task remains alive.
        snapshot = client.get("/improve/jobs")
        assert snapshot.status_code == 200
        assert snapshot.json()["active_run_id"] == run_id
        detail = client.get(f"/improve/jobs/{run_id}")
        assert detail.status_code == 200 and detail.json()["job"]["status"] == "running"
        premature_review = client.post(
            f"/improve/jobs/{run_id}/review",
            json={"decision": "approved", "request_id": "too_early"},
        )
        assert premature_review.status_code == 409

        cancel = client.post(f"/improve/jobs/{run_id}/cancel")
        assert cancel.status_code == 202, cancel.text
        assert cancel.json()["job"]["stop_requested"] is True
        for _ in range(50):
            latest = client.get(f"/improve/jobs/{run_id}").json()["job"]
            if latest["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert latest["status"] == "cancelled"
        assert (tmp_path / "runs" / run_id / "gepa.stop").is_file()


def test_experiment_review_route_claims_idempotently_and_exposes_saved_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    run_id = "candidate-123"
    _completed(store, run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"manifest":{"evaluation":{}}}')
    approval_started = threading.Event()
    finish = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, selected_run_id: str, kind: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = kind, options
        assert approval
        approval_started.set()
        deadline = asyncio.get_running_loop().time() + 2
        while not finish.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            selected_run_id,
            {"status": "approved", "stage": "approved", "decision": "approved"},
            owner_id=manager.owner_id,
            expected_statuses=("approving",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    _app, client = _app_client(tmp_path, store)
    try:
        with client:
            detail = client.get(f"/improve/jobs/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["comparison"] == {"manifest": {"evaluation": {}}}

            review = {"decision": "approved", "request_id": "approve_once"}
            first = client.post(f"/improve/jobs/{run_id}/review", json=review)
            assert first.status_code == 202, first.text
            assert first.json()["job"]["status"] == "approving"
            assert approval_started.wait(timeout=2)
            retry = client.post(f"/improve/jobs/{run_id}/review", json=review)
            assert retry.status_code == 202
            assert retry.json()["job"]["status"] == "approving"
            assert "review_request_id" not in retry.json()["job"]
            busy = client.post("/improve/jobs", json={"request_id": "second_run", "gepa_budget": "light"})
            assert busy.status_code == 409
            finish.set()
            for _ in range(50):
                final = client.get(f"/improve/jobs/{run_id}").json()["job"]
                if final["status"] == "approved":
                    break
                time.sleep(0.01)
            assert final["status"] == "approved"
    finally:
        finish.set()


def test_rejection_records_decision_and_preserves_candidate_and_comparison(tmp_path: Path) -> None:
    store = MemoryStore()
    run_id = "reject-http-run"
    _completed(store, run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / f"{run_id}.json"
    candidate.write_bytes(b"candidate artifact")
    comparison = candidates / f"{run_id}.comparison.json"
    comparison.write_text('{"manifest":{"review":"evidence"}}')
    _app, client = _app_client(tmp_path, store)
    with client:
        path = f"/improve/jobs/{run_id}/review"
        request = {"decision": "rejected", "request_id": "reject_once"}
        response = client.post(path, json=request)
        assert response.status_code == 202
        assert response.json()["job"]["status"] == "rejected"
        retry = client.post(path, json=request)
        assert retry.status_code == 202
        assert candidate.read_bytes() == b"candidate artifact"
        detail = client.get(f"/improve/jobs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["comparison"] == {"manifest": {"review": "evidence"}}


def test_database_cancel_remains_authoritative_when_stop_file_signal_fails(tmp_path, monkeypatch):
    from fleet_rlm.optimize.gepa import _check_cancelled, _database_stop_requested, _DatabaseStopper

    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)

        def fail_signal(*_args):
            raise OSError("cannot create stop marker")

        monkeypatch.setattr("fleet_rlm.experiments.request_run_stop", fail_signal)
        with pytest.raises(OSError, match="stop marker"):
            await manager.cancel("s1-run")
        stop_file = tmp_path / "runs" / "s1-run" / "gepa.stop"
        assert not stop_file.exists()
        # The stop file is absent, so the durable ledger fence must carry the cancellation on its own.
        _check_cancelled(stop_file)
        assert _database_stop_requested(store, "s1-run")
        assert _DatabaseStopper(store, "s1-run")(None)
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "completed"}})
        await manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=False)
        assert store.get_optimization_run("s1-run")["status"] == "cancelled"

    asyncio.run(scenario())


def test_managed_persistence_failure_keeps_sanitized_worker_diagnostic(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        failure = {"type": "OptimizationPersistenceError", "detail": "Optimization failed; exception details omitted."}
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "failed", "failure": failure}})
        await manager._finish("s1-run", SimpleNamespace(returncode=1), None, approval=False)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "failed"
        assert record["failure"] == failure
        assert record["candidate_version"] is None

    asyncio.run(scenario())


def test_polled_recovery_is_throttled_to_one_attempt_per_interval(tmp_path: Path, monkeypatch) -> None:
    """Repeated polls cost one reconcile, not one per client refresh."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        attempts: list[float] = []
        real = ExperimentManager._reconcile_stale_runs

        async def counted(manager_self) -> None:
            attempts.append(time.monotonic())
            await real(manager_self)

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        for _ in range(5):
            await manager.list()
        assert len(attempts) == 1

    asyncio.run(scenario())


def test_recovery_interval_governs_when_the_next_attempt_is_due(tmp_path: Path, monkeypatch) -> None:
    """An elapsed interval admits exactly one new attempt; an unexpired one admits none."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        attempts: list[float] = []

        async def counted(*_args) -> None:
            attempts.append(time.monotonic())

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        clock = [1000.0]
        monkeypatch.setattr("fleet_rlm.experiments.time.monotonic", lambda: clock[0])
        await manager.list()
        assert len(attempts) == 1
        clock[0] += _RECOVERY_INTERVAL_SECONDS - 0.1
        await manager.list()
        assert len(attempts) == 1, "an unexpired interval must not reconcile again"
        clock[0] += 0.2
        await manager.list()
        assert len(attempts) == 2, "an expired interval must reconcile exactly once"

    asyncio.run(scenario())


def test_failed_recovery_still_counts_toward_the_interval(tmp_path: Path, monkeypatch) -> None:
    """A broken database is retried on the next interval, not on every poll."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def fail(*_args) -> None:
            calls.append(1)
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", fail)
        with pytest.raises(RuntimeError, match="database unavailable"):
            await manager.list()
        await manager.list()
        assert len(calls) == 1

    asyncio.run(scenario())


def test_drain_bypasses_the_poll_throttle_so_shutdown_is_not_delayed(tmp_path: Path, monkeypatch) -> None:
    """A closed admission keeps reconciling on every status call during launcher drain."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def counted(*_args) -> None:
            calls.append(1)

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        await manager.begin_drain()
        await manager.status()
        await manager.status()
        assert len(calls) == 3

    asyncio.run(scenario())


def test_admission_reconciles_without_consuming_the_poll_interval(tmp_path: Path, monkeypatch) -> None:
    """start() bypasses the poll throttle. Admission is refused so no worker is spawned."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def counted(*_args) -> None:
            calls.append(1)

        async def refuse() -> None:
            raise ExperimentBusyError("admission refused for the test")

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        monkeypatch.setattr(ExperimentManager, "_available", staticmethod(lambda: refuse()))
        clock = [1000.0]
        monkeypatch.setattr("fleet_rlm.experiments.time.monotonic", lambda: clock[0])
        await manager.initialize()
        assert calls == [1], "startup reconciles once"
        # The first poll reconciles and opens the window; a later one inside it does not.
        await manager.list()
        assert len(calls) == 2
        clock[0] += 0.1
        await manager.list()
        assert len(calls) == 2, "a poll inside the interval is throttled"
        with pytest.raises(ExperimentBusyError, match="admission refused"):
            await manager.start("request-1", ExperimentOptions())
        assert len(calls) == 3, "admission must reconcile even during the poll interval"
        assert not manager.busy, "no worker may be spawned by this test"

    asyncio.run(scenario())


class _LabelStore(MemoryStore):
    """MemoryStore plus the read models the Improve routes use."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[dict[str, Any]] = []
        self.references: dict[str, dict[str, Any]] = {}
        self.new_labels_since: list[str | None] = []
        self.ledger: dict[str, Any] = {"approved": None, "approving": []}

    def approved_program(self) -> dict[str, Any]:
        return self.ledger

    def improve_queue(self, limit: int) -> list[dict[str, Any]]:
        return self.queue[:limit]

    def turn_reference(self, turn_id: str) -> dict[str, Any] | None:
        return self.references.get(turn_id)

    def new_review_labels(self, since: str | None) -> int:
        self.new_labels_since.append(since)
        return 4


def _example(group: str, critical: bool = False) -> SimpleNamespace:
    return SimpleNamespace(source_group=group, critical=critical)


def _queue_row(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess_a",
        "turn_id": "turn-1",
        "task": "Total revenue?",
        "answer": "Revenue was $1,250.50 across 42 orders",
        "review_status": None,
        "expected_answer": None,
        "expected_values": None,
        "critical": None,
        "source_group": None,
        "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
        **overrides,
    }


def test_readiness_reports_missing_requirements_and_new_labels_since_the_last_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _LabelStore()
    _completed(store, "optimize-earlier", created_at="2026-10-01T00:00:00+00:00")
    examples = [_example(f"sess_{index % 2}") for index in range(5)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, store)
    with client:
        report = client.get("/improve/readiness").json()
    assert report["ready"] is False
    assert (report["examples"], report["groups"], report["critical"]) == (5, 2, 0)
    assert len(report["missing"]) == 3
    assert report["new_labels_since_last_run"] == 4
    assert store.new_labels_since == ["2026-10-01T00:00:00+00:00"]


def test_new_labels_count_from_the_latest_run_that_finished_not_a_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _LabelStore()
    _completed(store, "optimize-finished", created_at="2026-10-01T00:00:00+00:00")
    _completed(store, "optimize-failed", status="failed", created_at="2026-10-09T00:00:00+00:00")
    _completed(store, "optimize-cancelled", status="cancelled", created_at="2026-10-10T00:00:00+00:00")
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: [])
    _app, client = _app_client(tmp_path, store)
    with client:
        assert client.get("/improve/readiness").status_code == 200
    assert store.new_labels_since == ["2026-10-01T00:00:00+00:00"]


@pytest.mark.parametrize("installed", ["missing", "changed", "matching", "never_approved"])
def test_readiness_reports_an_installed_program_that_contradicts_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str
) -> None:
    store = _LabelStore()
    store.ledger = {
        "approved": None if installed == "never_approved" else hashlib.sha256(b"approved").hexdigest(),
        "approving": [],
    }
    if installed in {"changed", "matching"}:
        (tmp_path / "fleet.json").write_bytes(b"approved" if installed == "matching" else b"tampered")
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, store)
    with client:
        report = client.get("/improve/readiness").json()
    broken = installed in {"missing", "changed"}
    assert report["ready"] is not broken
    assert any("approved global program" in item for item in report["missing"]) is broken


def test_readiness_is_green_with_enough_groups_and_a_critical_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, _LabelStore())
    with client:
        report = client.get("/improve/readiness").json()
    assert report["ready"] is True and report["missing"] == []
    assert sum(report["split_sizes"].values()) == 8 and all(report["split_sizes"].values())


def test_queue_lists_turns_across_sessions_with_label_state_and_extracted_figures(tmp_path: Path) -> None:
    store = _LabelStore()
    store.queue = [
        _queue_row(),
        _queue_row(
            session_id="sess_b",
            turn_id="turn-2",
            review_status="approved",
            expected_answer="1250.5",
            expected_values=["1250.5"],
            critical=True,
            source_group="sess_b",
        ),
    ]
    _app, client = _app_client(tmp_path, store)
    with client:
        items = client.get("/improve/queue").json()["items"]
        assert client.get("/improve/queue?limit=0").status_code == 422
    assert [item["session_id"] for item in items] == ["sess_a", "sess_b"]
    assert items[0]["label"] == "unlabelled" and items[0]["figures"] == ["1250.50", "42"]
    assert items[1]["label"] == "approved" and items[1]["critical"] is True and items[1]["group"] == "sess_b"


def test_label_route_resolves_the_trace_from_the_stored_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _LabelStore()
    store.references["turn-1"] = {"session_id": "sess_a", "turn_id": "turn-1", "trace_id": "tr-1"}
    store.references["turn-2"] = {"session_id": "sess_a", "turn_id": "turn-2", "trace_id": None}
    calls: list[tuple[Any, ...]] = []

    def fake_label(store_arg: Any, trace_id: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((store_arg, trace_id, kwargs))
        return {
            "review_status": kwargs["status"],
            "expected_answer": kwargs["answer"],
            "expected_values": kwargs["values"] or [],
            "critical": bool(kwargs["critical"]),
            "source_group": kwargs["group"] or "sess_a",
        }

    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_turn", fake_label)
    _app, client = _app_client(tmp_path, store)
    with client:
        saved = client.post(
            "/improve/labels",
            json={"turn_id": "turn-1", "expected_answer": "1250.5", "expected_values": ["1250.5"], "critical": True},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json() == {
            "turn_id": "turn-1",
            "trace_id": "tr-1",
            "status": "approved",
            "expected_answer": "1250.5",
            "expected_values": ["1250.5"],
            "critical": True,
            "group": "sess_a",
        }
        assert client.post("/improve/labels", json={"turn_id": "missing"}).status_code == 404
        assert client.post("/improve/labels", json={"turn_id": "turn-2"}).status_code == 422
        assert client.post("/improve/labels", json={"turn_id": "turn-1", "status": "maybe"}).status_code == 422
    assert [(call[0], call[1]) for call in calls] == [(store, "tr-1")]


def test_label_route_reports_a_failed_synchronization_with_its_recovery_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleet_rlm.optimize.evaluation import EvaluationError

    store = _LabelStore()
    store.references["turn-1"] = {"session_id": "sess_a", "turn_id": "turn-1", "trace_id": "tr-1"}

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise EvaluationError("MLflow labels were saved, but Lakebase curation failed (OperationalError)")

    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_turn", fail)
    _app, client = _app_client(tmp_path, store)
    with client:
        response = client.post("/improve/labels", json={"turn_id": "turn-1"})
    assert response.status_code == 503 and "Lakebase curation failed" in response.text


def test_history_lists_approved_versions_and_completed_evaluations(tmp_path: Path) -> None:
    store = _LabelStore()
    scores = {"baseline": {"dev": 0.4, "holdout": 0.5}, "candidate": {"dev": 0.6, "holdout": 0.7}}
    _completed(store, "optimize-approved", status="approved", scores=scores)
    _completed(store, "optimize-waiting")
    _completed(store, "sess_old-approved", status="approved", legacy_session_id="sess_old", scores=scores)
    _completed(store, "evaluate-rescore-done", kind="evaluate_rescore", scores=scores)
    _app, client = _app_client(tmp_path, store)
    with client:
        history = client.get("/improve/history").json()
    assert [job["run_id"] for job in history["versions"]] == ["optimize-approved"]
    assert history["versions"][0]["scores"] == scores
    assert [job["run_id"] for job in history["evaluations"]] == ["evaluate-rescore-done"]


def test_only_the_optimize_kind_exists_yet(tmp_path: Path) -> None:
    _app, client = _app_client(tmp_path, _LabelStore())
    with client:
        response = client.post("/improve/jobs", json={"request_id": "rescore", "kind": "evaluate_rescore"})
        assert response.status_code == 422
        assert client.post("/improve/jobs", json={"request_id": "bad", "kind": "deploy"}).status_code == 422


def test_a_legacy_per_session_candidate_stays_readable_and_rejectable_but_never_approvable(tmp_path: Path) -> None:
    store = _LabelStore()
    _completed(store, "sess_old-run", legacy_session_id="sess_old")
    _app, client = _app_client(tmp_path, store)
    with client:
        listed = client.get("/improve/jobs").json()["jobs"]
        assert listed[0]["legacy_session_id"] == "sess_old"
        refused = client.post(
            "/improve/jobs/sess_old-run/review", json={"decision": "approved", "request_id": "approve-legacy"}
        )
        assert refused.status_code == 409 and "globally" in refused.json()["detail"]
        assert store.get_optimization_run("sess_old-run")["status"] == "completed"
        rejected = client.post(
            "/improve/jobs/sess_old-run/review", json={"decision": "rejected", "request_id": "reject-legacy"}
        )
        assert rejected.status_code == 202 and rejected.json()["job"]["status"] == "rejected"


def test_budget_exhausted_is_a_terminal_worker_outcome_that_keeps_no_candidate(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        store.create_optimization_run("budget-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run(
            "budget-run", {"worker_outcome": {"status": "budget_exhausted", "candidate_version": None}}
        )
        await manager._finish("budget-run", SimpleNamespace(returncode=0), None, approval=False)
        record = store.get_optimization_run("budget-run")
        assert record["status"] == "budget_exhausted" and record["stage"] == "budget_exhausted"
        assert record["candidate_version"] is None and not manager.busy

    asyncio.run(scenario())


def test_improve_routes_replace_the_session_scoped_experiment_routes(tmp_path: Path) -> None:
    app, _client = _app_client(tmp_path, _LabelStore())
    paths = set(app.openapi()["paths"])
    assert {
        "/improve/readiness",
        "/improve/queue",
        "/improve/labels",
        "/improve/jobs",
        "/improve/jobs/{run_id}",
        "/improve/jobs/{run_id}/cancel",
        "/improve/jobs/{run_id}/review",
        "/improve/history",
    } <= paths
    assert not any("/experiments" in path and path.startswith("/sessions") for path in paths)
