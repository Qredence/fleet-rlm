"""Turn artifact validation, learned-note persistence, teardown classification, and failure formatting."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.contracts import SkillAdmissionError
from fleet_rlm.engine.turn_prepare import _turn_environment
from fleet_rlm.observability import phase_span
from fleet_rlm.sandbox.interpreter import StructuredResponseError
from fleet_rlm.sandbox.manager import CLEANUP_STOPPED, CLEANUP_UNCONFIRMED
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.sandbox.snapshots import MAX_REFUSAL_CHARS, SnapshotUnavailableError
from fleet_rlm.store import IdempotencyConflictError

if TYPE_CHECKING:
    from fleet_rlm.session import SessionRuntime

_LOGGER = logging.getLogger(__name__)
# The field that carries the teardown result on the stored Turn, its span, and the terminal event.
_CLEANUP_ATTRIBUTE = "cleanup"


def _stop_terminal_turn(manager: Any) -> str:
    """Stop a terminal Turn's sandbox work once and return the cleanup outcome it recorded."""
    try:
        manager.request_cancellation()
    except Exception:
        unconfirmed = True
    else:
        # A watchdog or disconnect may have run the teardown first and recorded its result.
        unconfirmed = getattr(manager, "cleanup_outcome", None) == CLEANUP_UNCONFIRMED
    if not unconfirmed:
        return CLEANUP_STOPPED
    _LOGGER.warning(
        "Turn teardown did not confirm the root sandbox stop for Session %s",
        getattr(manager, "session_id", "unknown"),
    )
    return CLEANUP_UNCONFIRMED


def _terminal_failure(
    runtime: SessionRuntime,
    manager: Any,
    *,
    session_id: str,
    turn_id: str,
    exc: BaseException,
    trace_id: str | None,
    started_at: float,
    persist: bool = True,
    skill_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one failed or cancelled outcome, tear the Turn down, and store it before acknowledgment."""
    cancelled = manager._cancelled.is_set() and not isinstance(exc, ExecutionOutcomeUnknownError)
    environment = _turn_environment(manager)
    if persist:
        _LOGGER.error("Turn %s failed for Session %s (%s)", turn_id, session_id, type(exc).__name__)
    else:
        _LOGGER.error(
            "Turn %s commit outcome is uncertain for Session %s (%s)", turn_id, session_id, type(exc).__name__
        )
    outcome = "cancelled" if cancelled else "failed"
    cleanup = _stop_terminal_turn(manager)
    duration_seconds = round(time.monotonic() - started_at, 3)
    failure: dict[str, Any] = {
        **(skill_intent or {}),
        "event": "turn_cancelled" if cancelled else "error",
        "turn_id": turn_id,
        "outcome": outcome,
        "duration_seconds": duration_seconds,
        "request_id": turn_id,
        "error": _failure_message(exc, turn_id),
        _CLEANUP_ATTRIBUTE: cleanup,
        **environment,
    }
    if trace_id:
        failure["trace_id"] = trace_id
    if persist:
        try:
            runtime.store.finish_turn(
                session_id,
                turn_id,
                outcome,
                failure,
                **({"ownership_epoch": runtime.ownership_epoch} if runtime.ownership_epoch is not None else {}),
            )
        except Exception as error:
            _LOGGER.warning("Failed to persist terminal Turn outcome (%s)", type(error).__name__)
    return failure


def _failure_message(exc: BaseException, turn_id: str) -> str:
    """Return the sanitized, caller-safe failure explanation for a client or stored Turn."""
    if isinstance(exc, IdempotencyConflictError):
        return str(exc)
    if isinstance(exc, SkillAdmissionError):
        return str(exc)
    if isinstance(exc, SnapshotUnavailableError):
        return str(exc)[:MAX_REFUSAL_CHARS]
    if isinstance(exc, (AdapterParseError, StructuredResponseError)):
        return (
            "The model returned an invalid structured response. "
            "Check the configured adapter and JSON mode. "
            f"See the trace for details (request {turn_id})."
        )
    return f"The Turn failed (request {turn_id})."


def _replayed(prior: dict[str, Any]) -> dict[str, Any]:
    """The stored outcome of an earlier attempt with the same idempotency key; never re-executed."""
    if prior["status"] == "completed":
        return {**deepcopy(prior["result"]), "turn_id": prior["turn_id"]}
    return {
        "event": "error",
        "request_id": prior["turn_id"],
        "error": "Prior turn has an uncertain or failed outcome; it was not replayed.",
    }


def _artifact_changes(before: dict[str, str], capture: dict[str, Any]) -> dict[str, Any]:
    """Compare host digests; unchanged content is retained, not evidence of a new write."""
    files = {entry["path"]: entry for entry in capture["files"]}
    inputs = {path: digest for path, digest in before.items() if path.startswith("artifacts/")}
    changes = {}
    complete = capture["complete"]
    for path in sorted(inputs.keys() | set(capture["paths"])):
        old = inputs.get(path)
        new = files.get(path, {}).get("sha256")
        if not complete:
            state = "unknown"
        elif new is None:
            state = "deleted"
        elif old is None:
            state = "created"
        else:
            state = "retained" if old == new else "modified"
        changes[path] = {"state": state, "input_sha256": old, "sha256": new}
    return {"complete": complete, "files": changes}


def _capture_artifact_view(evidence: Any) -> dict[str, Any]:
    """Validate one host evidence capture and retain its logical inventory for Turn replay."""
    from fleet_rlm.execution_evidence import (
        MAX_ARTIFACT_BYTES,
        MAX_ARTIFACT_EVIDENCE_BYTES,
        MAX_ARTIFACTS,
        artifact_path,
    )

    if not isinstance(evidence, dict):
        raise RuntimeError("Artifact evidence is invalid")
    observed = evidence.get("artifacts")
    if (
        not isinstance(observed, dict)
        or not isinstance(observed.get("complete"), bool)
        or not isinstance(observed.get("files"), dict)
        or evidence.get("children_complete") is not True
        or not isinstance(evidence.get("children"), list)
    ):
        raise RuntimeError("Artifact capture is incomplete")
    files = observed["files"]
    complete = observed["complete"]
    paths = observed.get("paths", sorted(files) if complete else None)
    if not isinstance(paths, list) or len(paths) > 10_000 or any(not isinstance(path, str) for path in paths):
        raise RuntimeError("Artifact inventory is incomplete")
    for path in paths:
        artifact_path(path)
    if not set(files).issubset(paths) or (complete and set(paths) != set(files)):
        raise RuntimeError("Artifact inventory does not match its capture")
    if len(files) > MAX_ARTIFACTS:
        raise RuntimeError("Artifact capture exceeds its file limit")
    child_sources: dict[str, tuple[str, str]] = {}
    for child in evidence["children"]:
        if not isinstance(child, dict) or child.get("status") == "running":
            raise RuntimeError("Artifact child capture is incomplete")
        receipts = child.get("artifacts", [])
        if not isinstance(receipts, list):
            raise RuntimeError("Artifact child capture is invalid")
        for receipt in receipts:
            if (
                not isinstance(receipt, dict)
                or not isinstance(receipt.get("root_path"), str)
                or not isinstance(receipt.get("agent_id"), str)
                or not isinstance(receipt.get("sha256"), str)
            ):
                raise RuntimeError("Artifact child capture is invalid")
            child_sources[receipt["root_path"]] = (receipt["agent_id"], receipt["sha256"])
    superseded = {path for path in child_sources if path not in paths}
    captured = []
    total = 0
    for path in sorted(files):
        entry = files[path]
        if not isinstance(path, str) or not isinstance(entry, dict):
            raise RuntimeError("Artifact capture is invalid")
        artifact_path(path)
        encoded = entry.get("base64")
        digest = entry.get("sha256")
        if not isinstance(encoded, str) or not isinstance(digest, str):
            raise RuntimeError("Artifact capture is invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("Artifact capture is invalid") from exc
        if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("Artifact capture hash or size is invalid")
        total += len(raw)
        if total > MAX_ARTIFACT_EVIDENCE_BYTES:
            raise RuntimeError("Artifact capture exceeds its byte limit")
        child_source = child_sources.get(path)
        if child_source is not None and child_source[1] != digest:
            superseded.add(path)
            child_source = None
        captured.append(
            {
                "path": path,
                "size_bytes": len(raw),
                "sha256": digest,
                "source": "child" if child_source else "workspace",
                **({"agent_id": child_source[0]} if child_source else {}),
            }
        )
    return {
        "complete": complete,
        "paths": sorted(set(paths)),
        "files": captured,
        **({"superseded_child_receipts": sorted(superseded)} if superseded else {}),
    }


def _write_learned_note(runtime: SessionRuntime, turn_id: str, note: str, trace_id: str | None) -> None:
    """Write the Turn's learned note to the shared mount, suppressing ordinary write failures."""
    try:
        with phase_span("turn.write_learned_note", note_chars=len(note)) as phase:
            path = runtime.sandbox_mgr.write_learned_note(turn_id, note, trace_id=trace_id)
            phase.set_outputs({"written": bool(path)})
    except Exception as exc:
        _LOGGER.warning("Learned note was not written to the shared mount (%s)", type(exc).__name__)
