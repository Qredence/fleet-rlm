"""One Turn, end to end: claim, prepare the inputs, run the engine, persist, and report.

Runs on a worker thread. Every outcome is reported through `emit` as a plain event dict, ending
with ``{"event": "done"}``; the caller decides how to deliver them (the API streams them as SSE).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import dspy
import mlflow
from mlflow.entities import SpanType
from mlflow.entities.trace_location import MlflowExperimentLocation

from fleet_rlm.engine.signatures import Route
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES, encode_snapshot
from fleet_rlm.sandbox.digests import unhashed_digest_size
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT
from fleet_rlm.store import IdempotencyConflictError

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntime

_LOGGER = logging.getLogger(__name__)
_TRACE_PREVIEW_CHARS = 200
_MAX_SHARED_NOTES = 8
_NOTE_DUPLICATE_SIMILARITY = 0.5

Emit = Callable[[dict[str, Any]], None]


def run_turn(
    runtime: SessionRuntime,
    settings: FleetSettings,
    *,
    session_id: str,
    turn_id: str,
    task: str,
    idempotency_key: str | None,
    mlflow_experiment_id: str | None,
    callback: Any,
    emit: Emit,
    record_replay: bool = False,
) -> None:
    """Run one Turn for a Session, claiming its id and emitting progress events through emit."""
    manager = runtime.sandbox_mgr
    previous_event_callback = manager.event_callback
    trace_id: str | None = None
    try:
        runtime.assert_lease_held()
        manager.event_callback = emit
        manager.reset_turn_budget()
        prior = runtime.harness.claim_turn(turn_id, idempotency_key or turn_id, task)
        if prior is not None:
            emit({**_replayed(prior), "turn_id": prior["turn_id"]})
            return
        # The destination travels with the span: `mlflow.set_experiment` is process-wide and would race
        # across Sessions that trace to different experiments.
        destination = MlflowExperimentLocation(mlflow_experiment_id) if mlflow_experiment_id else None
        with mlflow.start_span(name="fleet_turn", span_type=SpanType.CHAIN, trace_destination=destination) as span:
            trace_id = getattr(span, "trace_id", None) if mlflow_experiment_id else None
            # Keep enough context on the trace if preparation fails. The complete prepared inputs
            # replace this small initial payload once they are available.
            span.set_inputs({"session_id": session_id, "task": task, "turn_id": turn_id})
            with mlflow.start_span(name="turn.prepare_inputs", span_type=SpanType.CHAIN):
                inputs = _prepare_inputs(runtime, session_id, task, record_replay=record_replay)
            replay_snapshot: tuple[str, bytes] | None = None
            if record_replay:
                from fleet_rlm.session import runtime_configuration

                with mlflow.start_span(name="turn.capture_replay", span_type=SpanType.CHAIN):
                    snapshot = {
                        "schema_version": 1,
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "task": task,
                        "workspace_manifest": inputs["manifest"],
                        "context_files": inputs["context_files"],
                        "skill_revision_sha256": hashlib.sha256(
                            json.dumps(
                                {
                                    path: digest
                                    for path, digest in sorted(inputs["context_files"].items())
                                    if path.startswith("skills/")
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                        "replay_files": inputs["replay_files"],
                        "skills_catalog": inputs["skills_catalog"],
                        "memory_summary": inputs["memory_summary"],
                        "session_history": inputs["session_history"],
                        "dataset_context": (runtime.context.to_descriptor() if runtime.context is not None else None),
                        "execution_mode": runtime.execution_mode,
                        "turn_timeout_seconds": manager.turn_timeout_seconds,
                        "sandbox_transport": SANDBOX_TRANSPORT,
                        "configuration": runtime_configuration(settings, runtime),
                        "provenance": {"fleet_rlm_version": version("fleet-rlm")},
                    }
                    replay_snapshot = encode_snapshot(snapshot)
            _describe_turn(span, runtime, settings, session_id, task, turn_id, idempotency_key, inputs, replay_snapshot)
            with dspy.context(callbacks=[*dspy.settings.callbacks, callback], disable_history=True):
                prediction = runtime.engine(
                    workspace_manifest=inputs["manifest"],
                    skills_catalog=inputs["skills_catalog"],
                    memory_summary=inputs["memory_summary"],
                    session_history=inputs["session_history"],
                    task=task,
                    execution_mode=runtime.execution_mode,
                    context=runtime.context,
                )
            answer = str(prediction.answer)
            usage = {
                name: getattr(prediction, name, None)
                for name in ("lm_calls", "sandbox_errors", "submit_rejections", "budget_breakdown")
            }
            learned_note = str(getattr(prediction, "learned_skill_note", ""))[:10_000]
            route = str(getattr(prediction, "route", Route.RLM.value))
            evidence = getattr(prediction, "execution_evidence", {})
            artifact_changes = _artifact_changes(inputs["context_files"], evidence)
            runtime.assert_lease_held()
            turn_record = {
                "task": task,
                "workspace_manifest": inputs["manifest"],
                "skills_catalog": inputs["skills_catalog"],
                "answer": answer,
                "route": route,
                "learned_skill_note": learned_note,
                "turn_id": turn_id,
                "idempotency_key": idempotency_key,
                "execution_mode": runtime.execution_mode,
                "trace_id": trace_id,
                **usage,
                "context_files": inputs["context_files"],
                "artifact_changes": artifact_changes,
                "replay_snapshot_selected": record_replay,
                "replay_snapshot_sha256": replay_snapshot[0] if replay_snapshot else None,
            }
            with mlflow.start_span(name="turn.commit", span_type=SpanType.CHAIN):
                artifacts = runtime.harness.commit_turn(
                    session_id=session_id,
                    turn_record=turn_record,
                    new_memory_note=learned_note,
                    replay_snapshot=replay_snapshot,
                )
            _write_learned_note(runtime, turn_id, learned_note, trace_id)
            span.set_outputs(
                {
                    "answer": answer,
                    "route": route,
                    # Keep usage and host receipts ahead of diagnostic artifact summaries.
                    **usage,
                    "execution_evidence": evidence,
                    "artifacts": artifacts,
                    "artifact_changes": artifact_changes,
                }
            )
            # Read after the engine ran: it reloads an approved program at the start of the Turn.
            span.set_attribute(
                "compiled_program_digest", getattr(getattr(runtime.engine, "compiled", None), "digest", "")
            )
            mlflow.update_current_trace(response_preview=answer[:_TRACE_PREVIEW_CHARS], tags={"fleet.route": route})
        completion: dict[str, Any] = {
            "event": "turn_complete",
            "answer": answer,
            "route": route,
            "learned_skill_note": learned_note,
            "artifacts": artifacts,
            **usage,
        }
        if trace_id:
            completion["trace_id"] = trace_id
        emit(completion)
    except Exception as exc:
        _LOGGER.error("Turn %s failed for Session %s (%s)", turn_id, session_id, type(exc).__name__)
        cancelled = manager._cancelled.is_set()
        failure: dict[str, Any] = {
            "event": "turn_cancelled" if cancelled else "error",
            "request_id": turn_id,
            "error": str(exc) if isinstance(exc, IdempotencyConflictError) else f"The Turn failed (request {turn_id}).",
        }
        if trace_id:
            failure["trace_id"] = trace_id
        with suppress(Exception):
            runtime.harness.store.finish_turn(session_id, turn_id, "cancelled" if cancelled else "failed", failure)
        emit(failure)
    finally:
        try:
            manager.end_turn()
        finally:
            # The stream's end marker must survive a failing sandbox cleanup.
            manager.event_callback = previous_event_callback
            emit({"event": "done"})


def _replayed(prior: dict[str, Any]) -> dict[str, Any]:
    """The stored outcome of an earlier attempt with the same idempotency key; never re-executed."""
    if prior["status"] == "completed":
        return prior["result"]
    return {
        "event": "error",
        "request_id": prior["turn_id"],
        "error": "Prior turn has an uncertain or failed outcome; it was not replayed.",
    }


def _artifact_changes(before: dict[str, str], evidence: dict[str, Any]) -> dict[str, Any]:
    """Compare host digests; unchanged content is retained, not evidence of a new write.

    The existing artifacts list remains the Session inventory. Missing bounded
    evidence cannot prove deletion, so those entries explicitly remain unknown.
    """
    observed = evidence.get("artifacts", {})
    complete = observed.get("complete") is True
    files = observed.get("files", {})
    inputs = {path: digest for path, digest in before.items() if path.startswith("artifacts/")}
    changes = {}
    for path in sorted(inputs.keys() | files.keys()):
        old = inputs.get(path)
        new = files.get(path, {}).get("sha256")
        if new is None:
            state = "deleted" if complete else "unknown"
        elif old is None:
            state = "created"
        else:
            state = "retained" if old == new else "modified"
        changes[path] = {"state": state, "input_sha256": old, "sha256": new}
    return {"complete": complete, "files": changes}


def _note_words(summary: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", summary.lower()))


def _distinct_notes(
    notes: list[dict[str, str]],
    *,
    limit: int = _MAX_SHARED_NOTES,
    threshold: float = _NOTE_DUPLICATE_SIMILARITY,
) -> list[dict[str, str]]:
    """Newest-first shared notes without restatements, at most ``limit`` of them.

    A summary whose word overlap (Jaccard) with a kept one reaches ``threshold`` is dropped, so one
    recipe saved by many Turns does not crowd out the prompt.
    """
    kept: list[tuple[dict[str, str], set[str]]] = []
    for note in notes:
        words = _note_words(note.get("summary", ""))
        if any(len(words & seen) >= threshold * len(words | seen) for _, seen in kept if words | seen):
            continue
        kept.append((note, words))
        if len(kept) == limit:
            break
    return [note for note, _ in kept]


def _prepare_inputs(
    runtime: SessionRuntime, session_id: str, task: str, *, record_replay: bool = False
) -> dict[str, Any]:
    with mlflow.start_span(name="turn.load_context", span_type=SpanType.CHAIN):
        memory_summary, session_history = runtime.harness.load_context(session_id)
    # Skills land first so their files are hashed; everything else remote arrives in one exec.
    with mlflow.start_span(name="turn.sync_skills", span_type=SpanType.CHAIN):
        runtime.skill_mgr.sync_local_skills_to_volume()
    with mlflow.start_span(name="turn.workspace_inputs", span_type=SpanType.CHAIN):
        remote = runtime.sandbox_mgr.turn_inputs()
    shared_notes = _distinct_notes(remote["notes"])
    if shared_notes:
        memory_summary = "\n".join(
            [
                *([memory_summary] if memory_summary else []),
                "Shared learned notes (unreviewed hints, never a verification reference; "
                "read /learned/<file> if relevant):",
                *(f"- {note['file']}: {note['summary']}" for note in shared_notes),
            ]
        )
    # Workspace files are model-writable, so they cannot approve executable programs.
    context_files = remote["digests"]
    files = sorted(context_files)
    replay_files: dict[str, str] = {}
    replay_bytes = 0
    # Only a selected replay needs the bytes; other Turns record the remotely computed digests.
    if record_replay:
        with mlflow.start_span(name="turn.replay_files", span_type=SpanType.CHAIN):
            for path in files:
                content = runtime.sandbox_mgr.read_workspace_file(path)
                digest, recorded = hashlib.sha256(content).hexdigest(), context_files[path]
                # A capped digest records only size and mtime; the replay bytes supply the real hash.
                capped_size = unhashed_digest_size(recorded)
                if capped_size is not None and capped_size == len(content):
                    context_files[path] = recorded = digest
                if digest != recorded:
                    raise ValueError(f"Workspace file changed while the Turn was prepared: {path}")
                replay_bytes += len(content)
                if replay_bytes > MAX_REPLAY_FILE_BYTES:
                    raise ValueError("Selected replay workspace exceeds the 8 MB snapshot limit")
                replay_files[path] = base64.b64encode(content).decode("ascii")
    with mlflow.start_span(name="turn.reviewed_memories", span_type=SpanType.CHAIN):
        memories = runtime.harness.memories(task, reviewed_only=True)
    with mlflow.start_span(name="turn.skill_catalog", span_type=SpanType.CHAIN):
        skills_catalog = runtime.skill_mgr.build_catalog_xml()
    # Keep the version-1 replay manifest paths-only. Environment hints are captured
    # with the other context so serving and immutable replay see the same inputs.
    if remote["environment"]:
        memory_summary = "\n".join(filter(None, (memory_summary, remote["environment"])))
    return {
        "manifest": ", ".join(f"/workspace/{path}" for path in files) or "/workspace/files (empty)",
        "context_files": context_files,
        "skills_catalog": skills_catalog,
        "memory_summary": memory_summary + "\n" + str(memories)[:12000],
        "session_history": session_history,
        "replay_files": replay_files,
    }


def _describe_turn(
    span: Any,
    runtime: SessionRuntime,
    settings: FleetSettings,
    session_id: str,
    task: str,
    turn_id: str,
    idempotency_key: str | None,
    inputs: dict[str, Any],
    replay_snapshot: tuple[str, bytes] | None,
) -> None:
    from fleet_rlm.session import runtime_configuration

    configuration = runtime_configuration(settings, runtime)
    # The trace keeps a small integrity reference; opted-in file bytes stay in Lakebase.
    span.set_inputs(
        {
            "session_id": session_id,
            "task": task,
            "turn_id": turn_id,
            "workspace_manifest": inputs["manifest"],
            "context_files": inputs["context_files"],
            "replay_snapshot_selected": replay_snapshot is not None,
            "replay_snapshot_sha256": replay_snapshot[0] if replay_snapshot else "",
        }
    )
    span.set_attributes(
        {
            "execution_mode": runtime.execution_mode,
            "turn_timeout_seconds": runtime.sandbox_mgr.turn_timeout_seconds,
            "sandbox_transport": SANDBOX_TRANSPORT,
            "configuration_digest": configuration["digest"],
            "model_settings": {
                name: value
                for name, value in configuration["values"].items()
                if name.startswith(("fleet_root_", "fleet_sub_"))
            },
        }
    )
    # Previews keep the trace list scannable; the full task and answer stay on the span. The Turn id
    # is the client request id so a stream event, a stored Turn, and its trace can be joined.
    mlflow.update_current_trace(
        metadata={"mlflow.trace.session": session_id},
        client_request_id=turn_id,
        tags={
            "fleet.execution_mode": runtime.execution_mode,
            "fleet.idempotency_key": idempotency_key or turn_id,
            "fleet.configuration_digest": configuration["digest"],
        },
        request_preview=task[:_TRACE_PREVIEW_CHARS],
    )


def _write_learned_note(runtime: SessionRuntime, turn_id: str, note: str, trace_id: str | None) -> None:
    try:
        with mlflow.start_span(name="turn.write_learned_note", span_type=SpanType.CHAIN):
            runtime.sandbox_mgr.write_learned_note(turn_id, note, trace_id=trace_id)
    except Exception as exc:
        _LOGGER.warning("Learned note was not written to the shared mount (%s)", type(exc).__name__)
