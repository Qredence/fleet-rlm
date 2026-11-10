"""API-owned job processes; optimization itself stays in the CLI worker.

There is one live process slot for every job kind, no queue, and no request-scoped task ownership.
PostgreSQL is the lifecycle ledger; subprocess output is advisory progress only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimization_runs import (
    ACTIVE_STATUSES,
    ExperimentOptions,
    public_run,
    read_comparison,
    reconcile_abandoned_running_runs,
    request_run_stop,
    validate_run_id,
    worker_settings_payload,
)
from fleet_rlm.store import Store

# The `fleet` subcommand that runs each job kind: one worker process, one lifecycle, different bodies.
_WORKER_COMMANDS: dict[str, tuple[str, ...]] = {
    "optimize": ("optimize",),
    "evaluate_rescore": ("optimize", "--evaluate", "rescore"),
    "evaluate_replay": ("optimize", "--evaluate", "replay"),
    "evaluate_synthetic": ("optimize", "--evaluate", "synthetic"),
}


class ExperimentBusyError(ValueError):
    """Admission or review conflicts with another owned operation."""


# Stale-run recovery opens a connection, takes the global advisory lease, and scans the whole
# ledger. Polled callers ask for it far more often than orphans can appear, so the observed
# paths are throttled while startup and shutdown drain stay immediate.
_RECOVERY_INTERVAL_SECONDS = 5.0


def _require_worker_pipes(process: asyncio.subprocess.Process) -> tuple[Any, Any, Any]:
    """Return the configured worker pipes, failing explicitly if subprocess setup is broken."""
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("Experiment worker subprocess pipes were not created")
    return process.stdin, process.stdout, process.stderr


class ExperimentManager:
    """Own subprocesses until they are reaped, regardless of client disconnection."""

    def __init__(self, settings: FleetSettings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.root = Path(settings.compiled_program_dir)
        self.owner_id = "api-" + uuid.uuid4().hex
        self.accepting = True
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._owner_lease: Connection | None = None
        self._recovered_at: float | None = None

    @property
    def busy(self) -> bool:
        """Keep admission closed through process exit, pipe draining, and final persistence."""
        return self._task is not None and not self._task.done()

    async def initialize(self) -> None:
        """Fence abandoned API workers without rerunning them."""
        async with self._lock:
            await self._ensure_owner_lease()
        # Unthrottled, and deliberately not counting as the polled attempt: a worker that still
        # held the global lease here must be re-checked on the next poll, not five seconds later.
        await self._reconcile_stale_runs()

    async def _ensure_owner_lease(self) -> None:
        """Register liveness before any admission; caller holds the manager lock."""
        if self._owner_lease is None and self.accepting:
            self._owner_lease = await asyncio.to_thread(self.store.acquire_optimization_owner_lease, self.owner_id)

    async def _recover_stale_runs(self) -> None:
        """Reconcile orphaned ledger rows at most once per interval.

        This is the polled-read entry point: `list`, `detail`, and an accepting `status` each
        reach it on every client refresh. Unthrottled, one TUI session cost a connection, the
        global advisory lease, and two ledger scans per second. A failed attempt still counts,
        because the marker is stamped before the body runs: an unreachable database is retried
        on the next interval instead of on every poll.

        Admission and review do not use this throttle. They depend on fresh reconciliation for
        correctness and are driven by explicit user actions, not by a timer.
        """
        now = time.monotonic()
        if self._recovered_at is not None and now - self._recovered_at < _RECOVERY_INTERVAL_SECONDS:
            return
        self._recovered_at = now
        await self._reconcile_stale_runs()

    async def _reconcile_stale_runs(self) -> None:
        """Reconcile orphaned ledger rows only while holding the worker's global lease."""
        async with self._lock:
            await self._ensure_owner_lease()
            if self.busy:
                return
            try:
                lease = await asyncio.to_thread(self.store.acquire_optimization_lease)
            except RuntimeError:
                # A worker still owns the lease. Only managed workers are ours to stop;
                # standalone CLI work remains in control of its own stop signal.
                for record in await asyncio.to_thread(self.store.list_optimization_runs):
                    if (
                        record["status"] != "running"
                        or not record.get("managed")
                        or record.get("owner_id") == self.owner_id
                        or await asyncio.to_thread(self.store.optimization_owner_alive, record["owner_id"])
                    ):
                        continue
                    try:
                        await asyncio.to_thread(self.store.request_optimization_stop, record["run_id"])
                    except ValueError:
                        continue
                    await asyncio.to_thread(request_run_stop, self.root, record["run_id"])
                return
            try:
                await asyncio.to_thread(reconcile_abandoned_running_runs, self.store, inactive_owner_id=self.owner_id)
                for record in await asyncio.to_thread(self.store.list_optimization_runs):
                    if record["status"] == "approving" or record.get("approval_pending"):
                        if (
                            record.get("managed")
                            and record["owner_id"] != self.owner_id
                            and await asyncio.to_thread(self.store.optimization_owner_alive, record["owner_id"])
                        ):
                            continue
                        await self._recover_approval(record)
            finally:
                await asyncio.to_thread(lease.close)

    async def list(self) -> dict[str, Any]:
        """Return snapshots without acquiring a runtime or provisioning a sandbox."""
        await self._recover_stale_runs()
        records = await asyncio.to_thread(self.store.list_optimization_runs)
        return {
            "jobs": [public_run(record) for record in records],
            "active_run_id": next(
                (record["run_id"] for record in records if record["status"] in ACTIVE_STATUSES), None
            ),
            "accepting": self.accepting,
        }

    async def get(self, run_id: str) -> dict[str, Any]:
        """Resolve the run identity before reading derived artifact paths.

        Raise FileNotFoundError for a missing run and ValueError for invalid identity;
        storage errors propagate.
        """
        record = await asyncio.to_thread(self.store.get_optimization_run, validate_run_id(run_id))
        if record is None:
            raise FileNotFoundError("Job was not found")
        return record

    async def detail(self, run_id: str) -> dict[str, Any]:
        """Serve saved comparison evidence, never arbitrary paths or worker output."""
        await self._recover_stale_runs()
        record = await self.get(run_id)
        comparison = await asyncio.to_thread(read_comparison, self.root, record)
        return {"job": public_run(record), "comparison": comparison}

    async def _available(self) -> None:
        """Check admission and worker availability without retaining the optimization lease.

        Raise ExperimentBusyError while draining, while owned work is unfinished, or
        when another worker holds the global lease. Other storage errors propagate.
        """
        if not self.accepting or self.busy:
            raise ExperimentBusyError("Experiment admission is draining or another operation is active")
        try:
            lease = await asyncio.to_thread(self.store.acquire_optimization_lease)
        except RuntimeError:
            raise ExperimentBusyError("An optimization worker is still active") from None
        await asyncio.to_thread(lease.close)

    async def start(
        self, request_id: str, options: ExperimentOptions, kind: str = "optimize", *, trigger: str = "manual"
    ) -> dict[str, Any]:
        """Persist an idempotent intent, and what started it, before spawning any paid work."""
        if kind == "evaluate_synthetic" or kind not in _WORKER_COMMANDS:
            raise ValueError(f"{kind} jobs are not available")
        await self._reconcile_stale_runs()
        async with self._lock:
            for prior in await asyncio.to_thread(self.store.list_optimization_runs):
                if prior["request_id"] == request_id:
                    if prior["options"] != options.model_dump() or prior["kind"] != kind:
                        raise ExperimentBusyError("Request ID was already used with different options")
                    return prior
            await self._available()
            run_id = kind.replace("_", "-") + "-" + uuid.uuid4().hex
            try:
                record = await asyncio.to_thread(
                    self.store.create_optimization_run,
                    run_id,
                    request_id,
                    options.model_dump(),
                    self.owner_id,
                    kind=kind,
                    managed=True,
                    trigger=trigger,
                )
            except ValueError as error:
                raise ExperimentBusyError(str(error)) from None
            self._task = asyncio.create_task(self._execute(run_id, kind, options, approval=False))
            return record

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """Accept only a running run's cooperative stop; completion wins after its transition."""
        record = await self.get(run_id)
        if record["status"] == "cancelled":
            return record
        if record["status"] != "running":
            raise ExperimentBusyError("Only a running job can be cancelled")
        # Persist intent first. The worker also checks this flag, so a marker write failure
        # cannot silently lose an accepted request or authorize a completed candidate.
        try:
            record = await asyncio.to_thread(self.store.request_optimization_stop, run_id)
        except ValueError as error:
            raise ExperimentBusyError(str(error)) from None
        await asyncio.to_thread(request_run_stop, self.root, run_id)
        return record

    async def review(self, run_id: str, decision: str, request_id: str) -> dict[str, Any]:
        """Serialize explicit review while the existing optimizer performs evidence checks."""
        await self._reconcile_stale_runs()
        async with self._lock:
            record = await self.get(run_id)
            if record.get("review_request_id") == request_id:
                if record.get("review_decision") != decision:
                    raise ExperimentBusyError("Review request ID was already used with another decision")
                if self.busy:
                    return record
                if record["status"] == "approving":
                    await self._available()
                    record = await self.get(run_id)
                    if record["status"] == "approving":
                        raise ExperimentBusyError("Approval recovery is still in progress")
                if record["status"] in {"approved", "rejected"}:
                    return record
                # A retry after an API restart may resume an approval whose worker ended
                # while the API owner was unavailable.
                if decision != "approved" or record["status"] != "completed":
                    return record
            if decision == "rejected":
                try:
                    return await asyncio.to_thread(self.store.reject_optimization_run, run_id)
                except ValueError as error:
                    raise ExperimentBusyError(str(error)) from None
            if decision != "approved":
                raise ValueError("decision must be approved or rejected")
            if record.get("legacy_session_id"):
                raise ExperimentBusyError("A per-Session candidate cannot be installed globally; run a new job")
            if record["kind"] != "optimize":
                raise ExperimentBusyError("Only an optimize job produces a candidate to install")
            await self._available()
            if record["status"] not in {"completed", "approving"}:
                raise ExperimentBusyError("Only a completed or recoverable job can be approved")
            try:
                await asyncio.to_thread(self.store.claim_optimization_review, run_id, self.owner_id)
            except ValueError as error:
                raise ExperimentBusyError(str(error)) from None
            record = await asyncio.to_thread(
                self.store.update_optimization_run,
                run_id,
                {
                    "review_request_id": request_id,
                    "review_decision": decision,
                    "worker_outcome": None,
                    "stage": "approving",
                },
                owner_id=self.owner_id,
                expected_statuses=("approving",),
            )
            if record is None:
                raise ExperimentBusyError("Job review ownership changed")
            self._task = asyncio.create_task(self._execute(run_id, record["kind"], ExperimentOptions(), approval=True))
            return record

    def _command(self, run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> Sequence[str]:
        """Build worker argv for a job or its approval, carrying managed ownership and progress flags."""
        return [
            sys.executable,
            "-m",
            "fleet_rlm.cli",
            *_WORKER_COMMANDS[kind],
            "--managed-run-id",
            run_id,
            "--managed-owner-id",
            self.owner_id,
            "--structured-events",
            "--mlflow",
            self.settings.mlflow_mode,
            *(["--approve", run_id] if approval else options.cli_arguments()),
        ]

    async def _execute(self, run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
        """Run a worker and drain both output pipes before finalizing its ledger outcome.

        Ordinary worker or I/O errors become sanitized failure metadata. Task
        cancellation requests a cooperative stop for search and is re-raised.
        Finalization and storage errors can still propagate.
        """
        process = None
        readers: list[asyncio.Task[None]] = []
        failure: dict[str, str] | None = None
        try:
            environment = dict(os.environ)
            for key in ("MLFLOW_TRACKING_URI", "MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_ID", "FLEET_SETUP_TOKEN"):
                environment.pop(key, None)
            process = await asyncio.create_subprocess_exec(
                *self._command(run_id, kind, options, approval=approval),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            stdin, stdout, stderr = _require_worker_pipes(process)
            readers = [
                asyncio.create_task(self._discard_output(stdout)),
                asyncio.create_task(self._discard_output(stderr)),
            ]
            stdin.write(worker_settings_payload(self.settings))
            await stdin.drain()
            stdin.close()
            await process.wait()
            await asyncio.gather(*readers)
        except asyncio.CancelledError:
            if not approval:
                await self.cancel(run_id)
            raise
        except Exception as error:
            failure = {"type": type(error).__name__, "detail": "Experiment worker failed; sensitive details omitted."}
        finally:
            # Ownership ends after all work and both pipes. Client requests never cancel this task.
            if process is not None:
                await asyncio.shield(process.wait())
            if readers:
                results = await asyncio.shield(asyncio.gather(*readers, return_exceptions=True))
                if any(isinstance(result, BaseException) for result in results):
                    failure = {"type": "WorkerOutput", "detail": "Experiment output could not be drained."}
            await self._finish(run_id, process, failure, approval=approval)

    async def _finish(
        self,
        run_id: str,
        process: asyncio.subprocess.Process | None,
        failure: dict[str, str] | None,
        *,
        approval: bool,
    ) -> None:
        """Finalize an active run still owned by this manager after its worker has drained.

        Unconfirmed or unsuccessful search exits fail; an accepted stop overrides a
        completed search. Approval uses artifact receipts to recover its outcome.
        Lookup and persistence errors propagate.
        """
        record = await self.get(run_id)
        if record.get("owner_id") != self.owner_id or record["status"] not in ACTIVE_STATUSES:
            return
        if approval:
            if process is None or process.returncode != 0:
                outcome = record.get("worker_outcome") or {}
                failure = (
                    failure
                    or outcome.get("failure")
                    or {"type": "ProcessExit", "detail": "Approval worker did not finish successfully."}
                )
            await self._recover_approval(record, failure=failure)
        else:
            outcome = record.get("worker_outcome")
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if (
                failure
                or process is None
                or process.returncode != 0
                or status not in {"completed", "cancelled", "failed", "budget_exhausted"}
            ):
                status = "failed"
                failure = (
                    failure
                    or (outcome.get("failure") if isinstance(outcome, dict) else None)
                    or {"type": "ProcessExit", "detail": "Worker exited without a confirmed successful outcome."}
                )
            if status == "completed" and record.get("stop_requested"):
                status = "cancelled"
            updates = {"status": status, "stage": status, "finished_at": datetime.now(UTC).isoformat()}
            if status != "completed":
                updates["candidate_version"] = None
            if failure:
                updates["failure"] = failure
            await asyncio.to_thread(
                self.store.update_optimization_run,
                run_id,
                updates,
                owner_id=self.owner_id,
                expected_statuses=("running",),
            )

    @staticmethod
    async def _discard_output(stream: asyncio.StreamReader) -> None:
        """Drain worker output to EOF without retaining or exposing its contents."""
        while await stream.read(8192):
            pass

    async def _recover_approval(self, record: dict[str, Any], *, failure: dict[str, str] | None = None) -> None:
        """Reconcile atomic install receipts; never install bytes during crash recovery."""
        run_id = validate_run_id(record["run_id"])
        directory = self.root / "candidates"
        manifest_path = directory / f"{run_id}.manifest.json"
        active_path = self.root / "fleet.json"
        status = "interrupted"
        updates: dict[str, Any] = {}
        try:
            manifest_name = os.path.abspath(manifest_path)
            if not manifest_name.startswith(os.path.abspath(directory) + os.sep):
                raise ValueError("approval manifest escapes candidate directory")
            manifest_path = Path(manifest_name)
            if directory.is_symlink() or manifest_path.is_symlink() or active_path.is_symlink():
                raise ValueError("invalid approval artifact")
            resolved_manifest = os.path.realpath(manifest_path)
            if not resolved_manifest.startswith(os.path.realpath(directory) + os.sep):
                raise ValueError("approval manifest escapes candidate directory")
            manifest_path = Path(resolved_manifest)
            if manifest_path.stat().st_size > 4_000_000:
                raise ValueError("invalid approval manifest")
            manifest = json.loads(await asyncio.to_thread(manifest_path.read_bytes))
            if (
                not isinstance(manifest, dict)
                or not isinstance(manifest.get("gate"), dict)
                or not isinstance(manifest.get("dependencies"), dict)
                or not isinstance(manifest.get("digest"), str)
                or len(manifest["digest"]) != 64
            ):
                raise ValueError("invalid approval manifest")
            if record.get("manifest_digest") and record["manifest_digest"] != manifest["digest"]:
                raise ValueError("recorded candidate identity changed")
            active = (
                hashlib.sha256(await asyncio.to_thread(active_path.read_bytes)).hexdigest()
                if active_path.exists()
                else ""
            )
            if manifest.get("version") != run_id:
                raise ValueError("approval manifest identity changed")
            if active == manifest.get("digest") and manifest.get("gate", {}).get("passed") is True:
                status = "approved"
                updates.update(decision="approved", decision_at=datetime.now(UTC).isoformat())
            elif active == record.get("incumbent_digest", manifest["dependencies"].get("incumbent_digest")):
                status = "completed"
            else:
                failure = failure or {
                    "type": "ApprovalInterrupted",
                    "detail": "Active artifact identity requires manual recovery.",
                }
        except (OSError, ValueError, TypeError):
            failure = failure or {
                "type": "ApprovalInterrupted",
                "detail": "Approval artifacts require manual recovery.",
            }
        updates.update(status=status, stage=status, approval_pending=False)
        if status == "approved" and failure is None:
            updates["failure"] = None
        if failure:
            updates["failure"] = failure
        await asyncio.to_thread(
            self.store.update_optimization_run,
            run_id,
            updates,
            owner_id=record["owner_id"],
            expected_statuses=(record["status"],),
        )

    async def begin_drain(self) -> dict[str, Any]:
        """Fence new starts before taking the active-work snapshot."""
        async with self._lock:
            self.accepting = False
        return await self.status()

    async def status(self) -> dict[str, Any]:
        """Report admission and live/unfinished worker ownership to the local launcher."""
        # Setup apply already holds this lock while it asks for status.
        if not self._lock.locked():
            # The launcher polls this every second while draining services and must observe
            # recovery promptly, so a closed admission bypasses the poll throttle.
            if self.accepting:
                await self._recover_stale_runs()
            else:
                await self._reconcile_stale_runs()
        records = await asyncio.to_thread(self.store.list_optimization_runs)
        active = [public_run(record) for record in records if record["status"] in ACTIVE_STATUSES]
        return {"accepting": self.accepting, "busy": self.busy or bool(active), "experiments": active}

    async def close(self) -> None:
        """Gracefully join owned work while its Store and MLflow destination remain available."""
        async with self._lock:
            self.accepting = False
        if self._task is not None:
            try:
                await asyncio.shield(self._task)
            finally:
                if self._task.done() and self._owner_lease is not None:
                    await asyncio.to_thread(self._owner_lease.close)
                    self._owner_lease = None
        elif self._owner_lease is not None:
            await asyncio.to_thread(self._owner_lease.close)
            self._owner_lease = None
