"""Global optimization ledger and approved program tracking for Store."""

from __future__ import annotations

import json
from typing import Any

import psycopg

from fleet_rlm.optimization_runs import validate_run_id
from fleet_rlm.store.schema import JOB_KINDS, _optimization_owner_id, _retry_once

_RUN_COLUMNS = (
    "run_id",
    "request_id",
    "kind",
    "options",
    "owner_id",
    "managed",
    "status",
    "stage",
    "stop_requested",
    "metadata",
    "created_at",
    "updated_at",
)


class OptimizationStoreMixin:
    """Global optimization run ledger and approved program state methods for Store."""

    pool: Any

    @_retry_once
    def approved_program(self) -> dict[str, Any]:
        """The installed global program as the ledger records it.

        `approved` is the manifest digest of the latest approved, non-legacy optimize job (None when
        none was ever approved); `approving` lists digests of approvals in flight, whose file may
        already be on disk before the ledger says so.
        """
        with self.pool.connection() as connection:
            rows = connection.execute(
                "SELECT status, metadata->>'manifest_digest' FROM fleet_lite_optimization_runs "
                "WHERE kind = 'optimize' AND status IN ('approved', 'approving') "
                "AND NOT (metadata ? 'legacy_session_id') "
                "ORDER BY updated_at DESC"
            ).fetchall()
        approved = next((row[1] for row in rows if row[0] == "approved" and row[1]), None)
        return {"approved": approved, "approving": [row[1] for row in rows if row[0] == "approving" and row[1]]}

    @_retry_once
    def create_optimization_run(
        self,
        run_id: str,
        request_id: str,
        options: dict[str, Any],
        owner_id: str,
        *,
        kind: str = "optimize",
        managed: bool = False,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Create one globally active job, or return an identical idempotent request retry."""
        run_id = validate_run_id(run_id)
        request_id = self._optimization_request_id(request_id)
        owner_id = self._optimization_owner_id(owner_id)
        if kind not in JOB_KINDS:
            raise ValueError(f"job kind must be one of {JOB_KINDS}")
        if not isinstance(options, dict):
            raise ValueError("optimization options must be an object")
        if type(managed) is not bool:
            raise ValueError("managed must be a boolean")
        options_json = self._optimization_json(options, "optimization options")
        try:
            with self.pool.connection() as connection, connection.transaction():
                inserted = connection.execute(
                    "INSERT INTO fleet_lite_optimization_runs "
                    "(run_id, request_id, kind, options, owner_id, managed, status, stage, metadata) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s, %s, 'running', 'preparing', "
                    "jsonb_build_object('trigger', %s::text)) "
                    "ON CONFLICT (request_id) DO NOTHING RETURNING *",
                    (run_id, request_id, kind, options_json, owner_id, managed, trigger),
                ).fetchone()
                if inserted is not None:
                    return self._optimization_run_record(inserted)
                prior = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE request_id = %s FOR UPDATE",
                    (request_id,),
                ).fetchone()
                if prior is None:
                    raise ValueError("optimization run request conflicts with an existing run")
                record = self._optimization_run_record(prior)
                if (
                    record["options"] != json.loads(options_json)
                    or record["kind"] != kind
                    or record["owner_id"] != owner_id
                    or record["managed"] is not managed
                ):
                    raise ValueError("optimization request_id was already used with different run details")
                return record
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("An optimization run is already active or the run identity is in use") from exc

    @_retry_once
    def get_optimization_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one job from the durable ledger, or None when it is absent."""
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM fleet_lite_optimization_runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return self._optimization_run_record(row) if row is not None else None

    @_retry_once
    def list_optimization_runs(self) -> list[dict[str, Any]]:
        """List the job history, newest update first."""
        with self.pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM fleet_lite_optimization_runs ORDER BY updated_at DESC, run_id"
            ).fetchall()
        return [self._optimization_run_record(row) for row in rows]

    @_retry_once
    def update_optimization_run(
        self,
        run_id: str,
        updates: dict[str, Any],
        *,
        owner_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Apply a metadata/status update only while the optional owner and status fence match."""
        run_id = validate_run_id(run_id)
        if not isinstance(updates, dict):
            raise ValueError("optimization updates must be an object")
        forbidden = {"run_id", "request_id", "kind", "options", "owner_id", "managed", "created_at", "updated_at"}
        if forbidden & updates.keys():
            raise ValueError("optimization run identity fields cannot be changed")
        if "status" in updates and (not isinstance(updates["status"], str) or not updates["status"].strip()):
            raise ValueError("optimization status must be nonempty text")
        if "stage" in updates and (not isinstance(updates["stage"], str) or not updates["stage"].strip()):
            raise ValueError("optimization stage must be nonempty text")
        if "stop_requested" in updates and type(updates["stop_requested"]) is not bool:
            raise ValueError("stop_requested must be a boolean")
        if expected_statuses is not None and (
            not isinstance(expected_statuses, tuple)
            or not expected_statuses
            or any(not isinstance(value, str) or not value for value in expected_statuses)
        ):
            raise ValueError("expected_statuses must be a nonempty tuple of statuses")
        metadata = {key: value for key, value in updates.items() if key not in {"status", "stage", "stop_requested"}}
        metadata_json = self._optimization_json(metadata, "optimization metadata")
        owner_id = self._optimization_owner_id(owner_id) if owner_id is not None else None
        statuses = list(expected_statuses) if expected_statuses is not None else None
        finishing_completed = updates.get("status") == "completed"
        try:
            with self.pool.connection() as connection:
                row = connection.execute(
                    "UPDATE fleet_lite_optimization_runs SET "
                    "status=CASE WHEN %s AND stop_requested THEN 'cancelled' ELSE COALESCE(%s,status) END,"
                    "stage=CASE WHEN %s AND stop_requested THEN 'cancelled' ELSE COALESCE(%s,stage) END,"
                    "stop_requested=COALESCE(%s,stop_requested),"
                    "metadata=metadata || %s::jsonb || CASE WHEN %s AND stop_requested "
                    "THEN '{\"candidate_version\":null}'::jsonb ELSE '{}'::jsonb END,updated_at=NOW() "
                    "WHERE run_id=%s "
                    "AND (%s::text IS NULL OR owner_id=%s) "
                    "AND (%s::text[] IS NULL OR status=ANY(%s::text[])) RETURNING *",
                    (
                        finishing_completed,
                        updates.get("status"),
                        finishing_completed,
                        updates.get("stage"),
                        updates.get("stop_requested"),
                        metadata_json,
                        finishing_completed,
                        run_id,
                        owner_id,
                        owner_id,
                        statuses,
                        statuses,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("Another optimization run is active") from exc
        return self._optimization_run_record(row) if row is not None else None

    @_retry_once
    def request_optimization_stop(self, run_id: str) -> dict[str, Any]:
        """Set a cooperative stop request for a running job; repeated requests are safe."""
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "UPDATE fleet_lite_optimization_runs SET stop_requested = TRUE, "
                "updated_at = CASE WHEN stop_requested THEN updated_at ELSE NOW() END "
                "WHERE run_id = %s AND status = 'running' RETURNING *",
                (run_id,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
        if row is None:
            raise ValueError("optimization run was not found")
        record = self._optimization_run_record(row)
        if record["status"] != "running":
            raise ValueError("only a running optimization can be stopped")
        return record

    @_retry_once
    def claim_optimization_review(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """Claim a completed candidate for approval, or take over its recoverable approving state."""
        run_id = validate_run_id(run_id)
        owner_id = self._optimization_owner_id(owner_id)
        try:
            with self.pool.connection() as connection, connection.transaction():
                prior = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE run_id = %s FOR UPDATE",
                    (run_id,),
                ).fetchone()
                if prior is None:
                    raise ValueError("optimization run was not found")
                record = self._optimization_run_record(prior)
                if record["status"] not in {"completed", "approving"}:
                    raise ValueError("only a completed or recoverable approving run can be reviewed")
                row = connection.execute(
                    "UPDATE fleet_lite_optimization_runs SET status = 'approving', owner_id = %s, updated_at = NOW() "
                    "WHERE run_id = %s AND status IN ('completed', 'approving') RETURNING *",
                    (owner_id, run_id),
                ).fetchone()
                if row is None:
                    raise ValueError("optimization review claim was lost")
                return self._optimization_run_record(row)
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("Another optimization run is active") from exc

    @_retry_once
    def reject_optimization_run(self, run_id: str) -> dict[str, Any]:
        """Record a rejection for a completed candidate without deleting its artifacts or history."""
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "UPDATE fleet_lite_optimization_runs SET status = 'rejected', "
                "metadata = metadata || CASE WHEN status = 'rejected' THEN '{}'::jsonb "
                "ELSE jsonb_build_object('decision', 'rejected', 'decision_at', NOW()) END, "
                "updated_at = CASE WHEN status = 'rejected' THEN updated_at ELSE NOW() END "
                "WHERE run_id = %s AND status IN ('completed', 'rejected') RETURNING *",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("only a completed optimization run can be rejected")
        return self._optimization_run_record(row)

    @staticmethod
    def _optimization_request_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("optimization request_id must contain 1-200 characters")
        return value

    _optimization_owner_id = staticmethod(_optimization_owner_id)

    @staticmethod
    def _optimization_json(value: dict[str, Any], label: str) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must contain JSON values") from exc

    @staticmethod
    def _optimization_run_record(row: Any) -> dict[str, Any]:
        values = dict(row) if isinstance(row, dict) else dict(zip(_RUN_COLUMNS, row, strict=False))
        metadata = values.pop("metadata", {}) or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, dict):
            raise ValueError("stored optimization metadata is invalid")
        values = {**metadata, **values}
        for name in ("created_at", "updated_at"):
            stamp = values.get(name)
            if hasattr(stamp, "isoformat"):
                values[name] = stamp.isoformat()
        return values
