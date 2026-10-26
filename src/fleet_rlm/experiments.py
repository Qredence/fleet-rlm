"""API-owned experiment processes; optimization itself stays in the CLI worker.

There is one live process slot, no queue, and no request-scoped task ownership.
PostgreSQL is the lifecycle ledger; subprocess output is advisory progress only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import Connection

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimization_runs import (
    ACTIVE_STATUSES,
    ExperimentOptions,
    import_legacy_runs,
    parse_event,
    public_run,
    read_comparison,
    reconcile_abandoned_running_runs,
    request_run_stop,
    validate_run_id,
    worker_settings_payload,
)
from fleet_rlm.store import Store
from fleet_rlm.workspace import validate_session_id


class ExperimentBusyError(ValueError):
    """Admission or review conflicts with another owned operation."""


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

    @property
    def busy(self) -> bool:
        """Keep admission closed through process exit, pipe draining, and final persistence."""
        return self._task is not None and not self._task.done()

    async def initialize(self) -> None:
        """Import old history and fence abandoned API workers without rerunning them."""
        async with self._lock:
            await self._ensure_owner_lease()
        await asyncio.to_thread(import_legacy_runs, self.store, self.root)
        await self._recover_stale_runs()

    async def _ensure_owner_lease(self) -> None:
        """Register liveness before any admission; caller holds the manager lock."""
        if self._owner_lease is None and self.accepting:
            self._owner_lease = await asyncio.to_thread(self.store.acquire_optimization_owner_lease, self.owner_id)

    async def _recover_stale_runs(self) -> None:
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
                        await asyncio.to_thread(
                            self.store.request_optimization_stop, record["session_id"], record["run_id"]
                        )
                    except ValueError:
                        continue
                    await asyncio.to_thread(request_run_stop, self.root, record["session_id"], record["run_id"])
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

    async def list(self, session_id: str) -> dict[str, Any]:
        """Return snapshots without acquiring a runtime or provisioning a sandbox."""
        await self._recover_stale_runs()
        records = await asyncio.to_thread(self.store.list_optimization_runs, validate_session_id(session_id))
        return {
            "experiments": [public_run(record) for record in records],
            "active_run_id": next(
                (record["run_id"] for record in records if record["status"] in ACTIVE_STATUSES), None
            ),
            "accepting": self.accepting,
        }

    async def get(self, session_id: str, run_id: str) -> dict[str, Any]:
        """Resolve Session/run identity before reading derived artifact paths."""
        record = await asyncio.to_thread(
            self.store.get_optimization_run, validate_session_id(session_id), validate_run_id(run_id)
        )
        if record is None:
            raise FileNotFoundError("Experiment was not found")
        return record

    async def detail(self, session_id: str, run_id: str) -> dict[str, Any]:
        """Serve saved comparison evidence, never arbitrary paths or worker output."""
        await self._recover_stale_runs()
        record = await self.get(session_id, run_id)
        comparison = await asyncio.to_thread(read_comparison, self.root, record)
        return {"experiment": public_run(record), "comparison": comparison}

    async def _available(self) -> None:
        if not self.accepting or self.busy:
            raise ExperimentBusyError("Experiment admission is draining or another operation is active")
        try:
            lease = await asyncio.to_thread(self.store.acquire_optimization_lease)
        except RuntimeError:
            raise ExperimentBusyError("An optimization worker is still active") from None
        await asyncio.to_thread(lease.close)

    async def start(self, session_id: str, request_id: str, options: ExperimentOptions) -> dict[str, Any]:
        """Persist an idempotent intent before spawning any paid work."""
        session_id = validate_session_id(session_id)
        await self._recover_stale_runs()
        async with self._lock:
            for prior in await asyncio.to_thread(self.store.list_optimization_runs, session_id):
                if prior["request_id"] == request_id:
                    if prior["options"] != options.model_dump():
                        raise ExperimentBusyError("Request ID was already used with different options")
                    return prior
            await self._available()
            run_id = session_id + "-" + uuid.uuid4().hex
            try:
                record = await asyncio.to_thread(
                    self.store.create_optimization_run,
                    session_id,
                    run_id,
                    request_id,
                    options.model_dump(),
                    self.owner_id,
                    managed=True,
                )
            except ValueError as error:
                raise ExperimentBusyError(str(error)) from None
            self._task = asyncio.create_task(self._execute(session_id, run_id, options, approval=False))
            return record

    async def cancel(self, session_id: str, run_id: str) -> dict[str, Any]:
        """Accept only a running run's cooperative stop; completion wins after its transition."""
        record = await self.get(session_id, run_id)
        if record["status"] == "cancelled":
            return record
        if record["status"] != "running":
            raise ExperimentBusyError("Only a running experiment can be cancelled")
        # Persist intent first. The worker also checks this flag, so a marker write failure
        # cannot silently lose an accepted request or authorize a completed candidate.
        try:
            record = await asyncio.to_thread(self.store.request_optimization_stop, session_id, run_id)
        except ValueError as error:
            raise ExperimentBusyError(str(error)) from None
        await asyncio.to_thread(request_run_stop, self.root, session_id, run_id)
        return record

    async def review(self, session_id: str, run_id: str, decision: str, request_id: str) -> dict[str, Any]:
        """Serialize explicit review while the existing optimizer performs evidence checks."""
        await self._recover_stale_runs()
        async with self._lock:
            record = await self.get(session_id, run_id)
            if record.get("review_request_id") == request_id:
                if record.get("review_decision") != decision:
                    raise ExperimentBusyError("Review request ID was already used with another decision")
                if self.busy:
                    return record
                if record["status"] == "approving":
                    await self._available()
                    record = await self.get(session_id, run_id)
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
                    return await asyncio.to_thread(self.store.reject_optimization_run, session_id, run_id)
                except ValueError as error:
                    raise ExperimentBusyError(str(error)) from None
            if decision != "approved":
                raise ValueError("decision must be approved or rejected")
            await self._available()
            if record["status"] not in {"completed", "approving"}:
                raise ExperimentBusyError("Only a completed or recoverable experiment can be approved")
            try:
                record = await asyncio.to_thread(
                    self.store.claim_optimization_review, session_id, run_id, self.owner_id
                )
            except ValueError as error:
                raise ExperimentBusyError(str(error)) from None
            record = await asyncio.to_thread(
                self.store.update_optimization_run,
                session_id,
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
                raise ExperimentBusyError("Experiment review ownership changed")
            self._task = asyncio.create_task(self._execute(session_id, run_id, ExperimentOptions(), approval=True))
            return record

    def _command(self, session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> Sequence[str]:
        return [
            sys.executable,
            "-m",
            "fleet_rlm.cli",
            "optimize",
            "--session",
            session_id,
            "--managed-run-id",
            run_id,
            "--managed-owner-id",
            self.owner_id,
            "--structured-events",
            "--mlflow",
            self.settings.mlflow_mode,
            *(["--approve", run_id] if approval else options.cli_arguments()),
        ]

    async def _execute(self, session_id: str, run_id: str, options: ExperimentOptions, *, approval: bool) -> None:
        process = None
        readers: list[asyncio.Task[None]] = []
        failure: dict[str, str] | None = None
        try:
            environment = dict(os.environ)
            for key in ("MLFLOW_TRACKING_URI", "MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_ID", "FLEET_SETUP_TOKEN"):
                environment.pop(key, None)
            process = await asyncio.create_subprocess_exec(
                *self._command(session_id, run_id, options, approval=approval),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None
            readers = [
                asyncio.create_task(self._read_output(process.stdout, session_id, run_id)),
                asyncio.create_task(self._discard_output(process.stderr)),
            ]
            process.stdin.write(worker_settings_payload(self.settings))
            await process.stdin.drain()
            process.stdin.close()
            await process.wait()
            await asyncio.gather(*readers)
        except asyncio.CancelledError:
            if not approval:
                await self.cancel(session_id, run_id)
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
            await self._finish(session_id, run_id, process, failure, approval=approval)

    async def _finish(
        self,
        session_id: str,
        run_id: str,
        process: asyncio.subprocess.Process | None,
        failure: dict[str, str] | None,
        *,
        approval: bool,
    ) -> None:
        record = await self.get(session_id, run_id)
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
                or status not in {"completed", "cancelled", "failed"}
            ):
                status = "failed"
                failure = failure or {
                    "type": "ProcessExit",
                    "detail": "Worker exited without a confirmed successful outcome.",
                }
            if status == "completed" and record.get("stop_requested"):
                status = "cancelled"
            updates = {"status": status, "stage": status, "finished_at": datetime.now(UTC).isoformat()}
            if status != "completed":
                updates["candidate_version"] = None
            if failure:
                updates["failure"] = failure
            await asyncio.to_thread(
                self.store.update_optimization_run,
                session_id,
                run_id,
                updates,
                owner_id=self.owner_id,
                expected_statuses=("running",),
            )

    async def _read_output(self, stream: asyncio.StreamReader, session_id: str, run_id: str) -> None:
        """Drain arbitrary-length lines without StreamReader's readline limit or raw log exposure."""
        pending = bytearray()
        oversized = False
        persistence_failed = False
        while chunk := await stream.read(8192):
            for piece in chunk.splitlines(keepends=True):
                if not oversized:
                    pending.extend(piece)
                    oversized = len(pending) > 20_000
                if piece.endswith(b"\n"):
                    if not oversized:
                        try:
                            event = parse_event(pending.decode("utf-8", errors="replace").rstrip("\r\n"))
                        except (ValueError, json.JSONDecodeError):
                            event = None
                        if (
                            event
                            and event.get("run_id") == run_id
                            and event["type"] != "terminal"
                            and not persistence_failed
                        ):
                            safe = {
                                key: value
                                for key, value in event.items()
                                if key
                                in {
                                    "type",
                                    "stage",
                                    "run_id",
                                    "iteration",
                                    "metric_calls_used",
                                    "metric_calls_delta",
                                    "metric_calls_remaining",
                                }
                            }
                            try:
                                saved = await asyncio.to_thread(
                                    self.store.update_optimization_run,
                                    session_id,
                                    run_id,
                                    {"progress": safe},
                                    owner_id=self.owner_id,
                                    expected_statuses=("running", "approving"),
                                )
                                if saved is None:
                                    raise ValueError("Experiment ownership changed")
                            except Exception:
                                # Keep draining: abandoning a reader can block the child's stdout,
                                # preventing its finally blocks and the parent's wait() from finishing.
                                persistence_failed = True
                                with suppress(OSError, ValueError):
                                    await asyncio.to_thread(request_run_stop, self.root, session_id, run_id)
                    pending.clear()
                    oversized = False
        if persistence_failed:
            raise RuntimeError("Experiment progress could not be persisted")

    @staticmethod
    async def _discard_output(stream: asyncio.StreamReader) -> None:
        while await stream.read(8192):
            pass

    async def _recover_approval(self, record: dict[str, Any], *, failure: dict[str, str] | None = None) -> None:
        """Reconcile atomic install receipts; never install bytes during crash recovery."""
        run_id = validate_run_id(record["run_id"])
        directory = self.root / "candidates"
        manifest_path = directory / f"{run_id}.manifest.json"
        active_path = self.root / f"{validate_session_id(record['session_id'])}.json"
        status = "interrupted"
        updates: dict[str, Any] = {}
        try:
            if directory.is_symlink() or manifest_path.is_symlink() or active_path.is_symlink():
                raise ValueError("invalid approval artifact")
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
            record["session_id"],
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
            await self._recover_stale_runs()
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
