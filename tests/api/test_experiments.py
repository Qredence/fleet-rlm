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
from fleet_rlm.experiments import ExperimentBusyError, ExperimentManager
from fleet_rlm.optimization_runs import ExperimentOptions


def test_failed_approval_exposes_sanitized_failure_and_success_clears_it(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1", "s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1", "s1-run", {"status": "approving", "candidate_version": "s1-run"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        manifest = {
            "version": "s1-run",
            "digest": hashlib.sha256(b"candidate").hexdigest(),
            "dependencies": {"incumbent_digest": hashlib.sha256(b"incumbent").hexdigest()},
            "gate": {"passed": True},
        }
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        active = tmp_path / "s1.json"
        active.write_bytes(b"incumbent")
        failure = {"type": "ValueError", "detail": "Approval failed; exception details omitted."}
        store.update_optimization_run("s1", "s1-run", {"worker_outcome": {"status": "failed", "failure": failure}})
        await manager._finish("s1", "s1-run", SimpleNamespace(returncode=1), None, approval=True)
        record = store.get_optimization_run("s1", "s1-run")
        assert record["status"] == "completed" and record["failure"] == failure
        active.write_bytes(b"candidate")
        store.update_optimization_run("s1", "s1-run", {"status": "approving", "worker_outcome": {"status": "approved"}})
        await manager._finish("s1", "s1-run", SimpleNamespace(returncode=0), None, approval=True)
        record = store.get_optimization_run("s1", "s1-run")
        assert record["status"] == "approved" and record["failure"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("manifest", [[], {"version": "s1-run", "gate": None}, {"version": "s1-run", "digest": ""}])
def test_invalid_approval_manifest_becomes_interrupted_without_install(tmp_path, manifest):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1", "s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1", "s1-run", {"status": "approving"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        await manager._recover_approval(store.get_optimization_run("s1", "s1-run"))
        assert store.get_optimization_run("s1", "s1-run")["status"] == "interrupted"
        assert not (tmp_path / "s1.json").exists()

    asyncio.run(scenario())


def test_progress_storage_failure_still_drains_worker_output(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1", "s1-run", "request", {}, manager.owner_id, managed=True)

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("private database failure")

        store.update_optimization_run = unavailable
        stream = asyncio.StreamReader()
        stream.feed_data(b'FLEET_EVENT {"type":"progress","run_id":"s1-run","metric_calls_used":1}\n')
        stream.feed_data(b"x" * 90_000 + b"\nordinary output\n")
        stream.feed_eof()
        with pytest.raises(RuntimeError, match="could not be persisted"):
            await asyncio.wait_for(manager._read_output(stream, "s1", "s1-run"), timeout=2)
        assert stream.at_eof()
        assert (tmp_path / "runs" / "s1-run" / "gepa.stop").exists()

    asyncio.run(scenario())


class MemoryStore:
    """Small ledger fake with the same ownership/status fences as Store."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
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
        session_id: str,
        run_id: str,
        request_id: str,
        options: dict[str, Any],
        owner_id: str,
        *,
        managed: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if any(row["status"] in {"running", "approving"} for row in self.rows.values()):
                raise ValueError("An optimization run is already active")
            if any(row["session_id"] == session_id and row["request_id"] == request_id for row in self.rows.values()):
                raise ValueError("optimization request already exists")
            now = datetime.now(UTC).isoformat()
            record = {
                "session_id": session_id,
                "run_id": run_id,
                "request_id": request_id,
                "options": dict(options),
                "owner_id": owner_id,
                "managed": managed,
                "status": "running",
                "stage": "preparing",
                "stop_requested": False,
                "created_at": now,
                "updated_at": now,
            }
            self.rows[(session_id, run_id)] = record
            return dict(record)

    def get_optimization_run(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get((session_id, run_id))
            return dict(row) if row else None

    def list_optimization_runs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [row for row in self.rows.values() if session_id is None or row["session_id"] == session_id]
            return [dict(row) for row in rows]

    def update_optimization_run(
        self,
        session_id: str,
        run_id: str,
        updates: dict[str, Any],
        *,
        owner_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get((session_id, run_id))
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

    def request_optimization_stop(self, session_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get((session_id, run_id))
            if row is None:
                raise ValueError("optimization run was not found")
            if row["status"] != "running":
                raise ValueError("only a running optimization can be stopped")
            row["stop_requested"] = True
            return dict(row)

    def claim_optimization_review(self, session_id: str, run_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get((session_id, run_id))
            if row is None or row["status"] not in {"completed", "approving"}:
                raise ValueError("only a completed or recoverable approving run can be reviewed")
            if any(other is not row and other["status"] in {"running", "approving"} for other in self.rows.values()):
                raise ValueError("Another optimization run is active")
            row.update(status="approving", owner_id=owner_id)
            return dict(row)

    def reject_optimization_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get((session_id, run_id))
            if row is None or row["status"] not in {"completed", "rejected"}:
                raise ValueError("only a completed optimization run can be rejected")
            row.update(status="rejected", decision="rejected")
            return dict(row)

    def import_optimization_run(self, session_id: str, record: dict[str, Any]) -> bool:
        with self._lock:
            key = (session_id, record["run_id"])
            if key in self.rows:
                return False
            if record.get("status") in {"running", "approving"} and any(
                row["status"] in {"running", "approving"} for row in self.rows.values()
            ):
                raise ValueError("Another optimization run is active")
            self.rows[key] = dict(record)
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


def _optimization_run(session_id: str, run_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "session_id": session_id,
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


def _completed(store: MemoryStore, session_id: str, run_id: str, *, gate: bool = True) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return store.import_optimization_run(
        session_id,
        {
            "session_id": session_id,
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
        },
    )


def test_api_owned_run_is_idempotent_global_and_drain_fences_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = options, approval
            started.set()
            await finish.wait()
            store.update_optimization_run(
                session_id,
                run_id,
                {"status": "approved" if approval else "completed", "stage": "completed"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",) if approval else ("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        options = ExperimentOptions(max_metric_calls=8)
        first = await manager.start("sess_one", "request-1", options)
        await started.wait()
        retry = await manager.start("sess_one", "request-1", options)
        assert retry["run_id"] == first["run_id"]
        with pytest.raises(ExperimentBusyError):
            await manager.start("sess_two", "request-2", options)

        draining = await manager.begin_drain()
        assert draining["accepting"] is False and draining["busy"] is True
        with pytest.raises(ExperimentBusyError):
            await manager.start("sess_two", "request-3", options)
        finish.set()
        await manager.close()
        final = await manager.get("sess_one", first["run_id"])
        assert final["status"] == "completed"
        assert manager.busy is False

    asyncio.run(scenario())


def test_api_owned_cancel_persists_stop_intent_and_cooperatively_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_execute(session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = options, approval
            started.set()
            while not (store.get_optimization_run(session_id, run_id) or {}).get("stop_requested"):
                await asyncio.sleep(0.01)
            stopped.set()
            store.update_optimization_run(
                session_id,
                run_id,
                {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
                owner_id=manager.owner_id,
                expected_statuses=("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.start("sess_cancel", "cancel-request", ExperimentOptions())
        await started.wait()
        accepted = await manager.cancel("sess_cancel", record["run_id"])
        assert accepted["stop_requested"] is True
        assert (tmp_path / "runs" / record["run_id"] / "gepa.stop").is_file()
        await stopped.wait()
        await manager._task
        assert (await manager.get("sess_cancel", record["run_id"]))["status"] == "cancelled"
        await manager.close()

    asyncio.run(scenario())


def test_review_retry_reuses_claim_and_manager_close_waits_for_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        _completed(store, "sess_review", "sess_review-candidate")
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> None:
            assert approval is True
            _ = options
            started.set()
            await finish.wait()
            store.update_optimization_run(
                session_id,
                run_id,
                {"status": "approved", "stage": "approved", "decision": "approved"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.review("sess_review", "sess_review-candidate", "approved", "review-key")
        await started.wait()
        retry = await manager.review("sess_review", "sess_review-candidate", "approved", "review-key")
        assert retry["status"] == "approving"
        assert retry["review_request_id"] == "review-key"
        with pytest.raises(ExperimentBusyError):
            await manager.review("sess_review", "sess_review-candidate", "rejected", "other-key")

        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert not closing.done()
        finish.set()
        await closing
        assert (await manager.get("sess_review", record["run_id"]))["status"] == "approved"

    asyncio.run(scenario())


def test_initialize_fences_abandoned_managed_worker_as_interrupted(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        run_id = "sess_recovery-run"
        store.import_optimization_run(
            "sess_recovery", _optimization_run("sess_recovery", run_id, request_id="old-request")
        )
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        record = store.get_optimization_run("sess_recovery", run_id)
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
        record = store.create_optimization_run("s1", "s1-run", "request", {}, owner.owner_id, managed=True)
        store.update_optimization_run("s1", "s1-run", {"status": status})
        worker = store.acquire_optimization_lease() if worker_started else None
        observer = ExperimentManager(_settings(tmp_path), store)
        try:
            await observer.initialize()
            await observer.list("s1")
            current = store.get_optimization_run("s1", record["run_id"])
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
        store.create_optimization_run("s1", "s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run(
            "s1",
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
            (tmp_path / "s1.json").write_bytes(b"candidate")
        update = store.update_optimization_run

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("final write unavailable")

        monkeypatch.setattr(store, "update_optimization_run", unavailable)
        manager._task = asyncio.create_task(
            manager._finish("s1", "s1-run", SimpleNamespace(returncode=0), None, approval=approval)
        )
        with pytest.raises(RuntimeError, match="final write unavailable"):
            await manager._task
        assert not manager.busy and store.optimization_owner_alive(manager.owner_id)
        monkeypatch.setattr(store, "update_optimization_run", update)
        await manager.list("s1")
        record = store.get_optimization_run("s1", "s1-run")
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

        def fail(*_args):
            raise RuntimeError("history unavailable")

        monkeypatch.setattr("fleet_rlm.experiments.import_legacy_runs", fail)
        with pytest.raises(RuntimeError, match="history unavailable"):
            await manager.initialize()
        assert store.optimization_owner_alive(manager.owner_id)
        await manager.close()
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


def test_recovery_keeps_workers_active_until_global_lease_is_released(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        managed = _optimization_run("sess_managed_orphan", "sess_managed_orphan-run", request_id="managed-request")
        standalone = _optimization_run(
            "sess_cli_orphan",
            "sess_cli_orphan-run",
            request_id="cli-request",
            owner_id="cli-owner",
            managed=False,
        )
        store.import_optimization_run(managed["session_id"], managed)
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        assert store.get_optimization_run(managed["session_id"], managed["run_id"])["status"] == "running"
        assert store.get_optimization_run(managed["session_id"], managed["run_id"])["stop_requested"] is True
        assert (tmp_path / "runs" / managed["run_id"] / "gepa.stop").is_file()
        assert store.get_optimization_run(managed["session_id"], managed["run_id"])["status"] == "running"
        store._lease.release()
        await manager.list(managed["session_id"])
        assert store.get_optimization_run(managed["session_id"], managed["run_id"])["status"] == "interrupted"
        await manager.close()

        cli_store = MemoryStore()
        cli_store.import_optimization_run(standalone["session_id"], standalone)
        cli_store._lease.acquire()
        cli_manager = ExperimentManager(_settings(tmp_path), cli_store)  # type: ignore[arg-type]
        await cli_manager.initialize()
        assert cli_store.get_optimization_run(standalone["session_id"], standalone["run_id"])["status"] == "running"
        cli_store._lease.release()
        snapshot = await cli_manager.list(standalone["session_id"])
        assert snapshot["experiments"][0]["status"] == "interrupted"
        await cli_manager.close()

    asyncio.run(scenario())


def test_legacy_approving_history_is_imported_inactive_then_recovered_idempotently(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        history = tmp_path / "experiments" / "sess_legacy"
        history.mkdir(parents=True)
        candidates = tmp_path / "candidates"
        candidates.mkdir()
        installed = b"installed candidate"
        digest = hashlib.sha256(installed).hexdigest()
        for suffix in ("approved", "interrupted"):
            current = f"sess_legacy-{suffix}"
            (history / f"{current}.json").write_text(
                json.dumps(
                    {
                        "session_id": "sess_legacy",
                        "run_id": current,
                        "request_id": f"request-{suffix}",
                        "options": {},
                        "owner_id": "legacy-owner",
                        "managed": False,
                        "status": "approving",
                        "stage": "approving",
                    }
                )
            )
            (candidates / f"{current}.manifest.json").write_text(
                json.dumps(
                    {
                        "version": current,
                        "digest": digest if suffix == "approved" else "other",
                        "gate": {"passed": True},
                        "dependencies": {"incumbent_digest": "incumbent"},
                    }
                )
            )
        (tmp_path / "sess_legacy.json").write_bytes(installed)
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        rows = await asyncio.to_thread(store.list_optimization_runs, "sess_legacy")
        assert {row["status"] for row in rows} == {"approved", "interrupted"}
        assert all(row["approval_pending"] is False for row in rows)
        await manager.initialize()
        assert {row["status"] for row in await asyncio.to_thread(store.list_optimization_runs, "sess_legacy")} == {
            "approved",
            "interrupted",
        }
        await manager.close()

    asyncio.run(scenario())


def test_same_approval_retry_reconciles_after_prior_owner_exits(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        session_id = "sess_retry_recovery"
        run_id = f"{session_id}-candidate"
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
        (tmp_path / f"{session_id}.json").write_bytes(installed)
        store.import_optimization_run(
            session_id,
            _optimization_run(
                session_id,
                run_id,
                request_id="candidate-request",
                owner_id="old-review-owner",
                status="approving",
                stage="approving",
                review_request_id="approve-once",
                review_decision="approved",
            ),
        )
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        with pytest.raises(ExperimentBusyError):
            await manager.review(session_id, run_id, "approved", "approve-once")
        assert store.get_optimization_run(session_id, run_id)["status"] == "approving"
        store._lease.release()
        result = await manager.review(session_id, run_id, "approved", "approve-once")
        assert result["status"] == "approved"
        assert manager._task is None
        await manager.close()

    asyncio.run(scenario())


def test_interrupted_run_keeps_its_saved_comparison_when_terminal_candidate_version_is_cleared(
    tmp_path: Path,
) -> None:
    session_id = "sess_interrupted_evidence"
    run_id = f"{session_id}-partial"
    store = MemoryStore()
    store.import_optimization_run(
        session_id,
        {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": "partial-request",
            "options": {},
            "owner_id": "imported",
            "managed": False,
            "status": "interrupted",
            "stage": "interrupted",
            "candidate_version": None,
        },
    )
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"report":"partial diagnostics"}')
    manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
    detail = asyncio.run(manager.detail(session_id, run_id))
    assert detail["comparison"] == {"report": "partial diagnostics"}


def test_managed_subprocess_gets_settings_on_stdin_and_only_bounded_progress_is_persisted(
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
                row = store.get_optimization_run("sess_worker", run_id)
                store.update_optimization_run(
                    "sess_worker",
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
            stdout = asyncio.StreamReader()
            stdout.feed_data(
                b"x" * 90_000
                + b"\n"
                + (
                    f'FLEET_EVENT {{"type":"progress","run_id":"{run_id}","stage":"search",'
                    '"metric_calls_used":4,"prompt":"must not persist"}\n'
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

        def capture_command(session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> list[str]:
            run_id_holder["run_id"] = run_id
            result = real_command(session_id, run_id, options, approval=approval)
            result.insert(result.index("--structured-events"), "--synthetic-output")
            return result

        manager._command = capture_command  # type: ignore[method-assign]
        await manager.start("sess_worker", "worker-request", ExperimentOptions())
        await manager._task
        record = await manager.get("sess_worker", run_id_holder["run_id"])
        assert record["status"] == "completed"
        assert record["progress"] == {
            "type": "progress",
            "stage": "search",
            "run_id": run_id_holder["run_id"],
            "metric_calls_used": 4,
        }
        assert "prompt" not in record["progress"]
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
                row = store.get_optimization_run("sess_reap", run_id["value"])
                store.update_optimization_run(
                    "sess_reap",
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
        record = await manager.start("sess_reap", "reap-request", ExperimentOptions())
        assert await asyncio.to_thread(spawned.wait, 2)
        # Let the output reader observe both the progress and terminal event while wait() is blocked.
        for _ in range(50):
            current = store.get_optimization_run("sess_reap", record["run_id"])
            if current.get("progress", {}).get("metric_calls_used") == 1:
                break
            await asyncio.sleep(0.01)
        assert current["status"] == "running"
        with pytest.raises(ExperimentBusyError):
            await manager.start("sess_other", "second", ExperimentOptions())

        allow_exit.set()
        await manager._task
        assert (await manager.get("sess_reap", record["run_id"]))["status"] == "completed"
        await manager.close()

    asyncio.run(scenario())


def test_experiment_routes_keep_run_after_response_and_cancel_only_by_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    started = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = options
        assert not approval
        started.set()
        deadline = asyncio.get_running_loop().time() + 5
        while (
            not (manager.store.get_optimization_run(session_id, run_id) or {}).get("stop_requested")
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            session_id,
            run_id,
            {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
            owner_id=manager.owner_id,
            expected_statuses=("running",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    _app, client = _app_client(tmp_path, store)
    with client:
        request = {"request_id": "run_once", "max_metric_calls": 9}
        response = client.post("/sessions/sess_http/experiments", json=request)
        assert response.status_code == 202, response.text
        record = response.json()["experiment"]
        run_id = record["run_id"]
        assert record["status"] == "running"
        assert not {"owner_id", "managed", "worker_settings", "pid"}.intersection(record)
        assert started.wait(timeout=2)

        repeated = client.post("/sessions/sess_http/experiments", json=request)
        assert repeated.status_code == 202
        assert repeated.json()["experiment"]["run_id"] == run_id
        conflict = client.post("/sessions/sess_other/experiments", json={"request_id": "other", "max_metric_calls": 3})
        assert conflict.status_code == 409
        invalid = client.post("/sessions/sess_http/experiments", json={"request_id": "bad", "unknown": "field"})
        assert invalid.status_code == 422

        # The response/request connection has ended, but the detached manager task remains alive.
        snapshot = client.get("/sessions/sess_http/experiments")
        assert snapshot.status_code == 200
        assert snapshot.json()["active_run_id"] == run_id
        detail = client.get(f"/sessions/sess_http/experiments/{run_id}")
        assert detail.status_code == 200 and detail.json()["experiment"]["status"] == "running"
        premature_review = client.post(
            f"/sessions/sess_http/experiments/{run_id}/review",
            json={"decision": "approved", "request_id": "too_early"},
        )
        assert premature_review.status_code == 409

        cancel = client.post(f"/sessions/sess_http/experiments/{run_id}/cancel")
        assert cancel.status_code == 202, cancel.text
        assert cancel.json()["experiment"]["stop_requested"] is True
        for _ in range(50):
            latest = client.get(f"/sessions/sess_http/experiments/{run_id}").json()["experiment"]
            if latest["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert latest["status"] == "cancelled"
        assert (tmp_path / "runs" / run_id / "gepa.stop").is_file()


def test_experiment_review_route_claims_idempotently_and_exposes_saved_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    run_id = "sess_candidate-123"
    _completed(store, "sess_review_http", run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"manifest":{"evaluation":{}}}')
    approval_started = threading.Event()
    finish = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, session_id: str, selected_run_id: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = options
        assert approval
        approval_started.set()
        deadline = asyncio.get_running_loop().time() + 2
        while not finish.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            session_id,
            selected_run_id,
            {"status": "approved", "stage": "approved", "decision": "approved"},
            owner_id=manager.owner_id,
            expected_statuses=("approving",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    _app, client = _app_client(tmp_path, store)
    try:
        with client:
            detail = client.get(f"/sessions/sess_review_http/experiments/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["comparison"] == {"manifest": {"evaluation": {}}}

            review = {"decision": "approved", "request_id": "approve_once"}
            first = client.post(f"/sessions/sess_review_http/experiments/{run_id}/review", json=review)
            assert first.status_code == 202, first.text
            assert first.json()["experiment"]["status"] == "approving"
            assert approval_started.wait(timeout=2)
            retry = client.post(f"/sessions/sess_review_http/experiments/{run_id}/review", json=review)
            assert retry.status_code == 202
            assert retry.json()["experiment"]["status"] == "approving"
            assert "review_request_id" not in retry.json()["experiment"]
            busy = client.post(
                "/sessions/sess_second/experiments", json={"request_id": "second_run", "gepa_budget": "light"}
            )
            assert busy.status_code == 409
            finish.set()
            for _ in range(50):
                final = client.get(f"/sessions/sess_review_http/experiments/{run_id}").json()["experiment"]
                if final["status"] == "approved":
                    break
                time.sleep(0.01)
            assert final["status"] == "approved"
    finally:
        finish.set()


def test_rejection_records_decision_and_preserves_candidate_and_comparison(tmp_path: Path) -> None:
    store = MemoryStore()
    run_id = "sess_reject_http-run"
    _completed(store, "sess_reject_http", run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / f"{run_id}.json"
    candidate.write_bytes(b"candidate artifact")
    comparison = candidates / f"{run_id}.comparison.json"
    comparison.write_text('{"manifest":{"review":"evidence"}}')
    _app, client = _app_client(tmp_path, store)
    with client:
        path = f"/sessions/sess_reject_http/experiments/{run_id}/review"
        request = {"decision": "rejected", "request_id": "reject_once"}
        response = client.post(path, json=request)
        assert response.status_code == 202
        assert response.json()["experiment"]["status"] == "rejected"
        retry = client.post(path, json=request)
        assert retry.status_code == 202
        assert candidate.read_bytes() == b"candidate artifact"
        detail = client.get(f"/sessions/sess_reject_http/experiments/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["comparison"] == {"manifest": {"review": "evidence"}}
