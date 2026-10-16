"""One Turn, end to end: claim, prepare the inputs, run the engine, persist, and report.

Runs on a worker thread. Every outcome is reported through `emit` as a plain event dict, ending
with ``{"event": "done"}``; the caller decides how to deliver them (the API streams them as SSE).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import dspy
import mlflow
from mlflow.entities import SpanType

from fleet_rlm.engine.signatures import Route
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT
from fleet_rlm.store import IdempotencyConflictError

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntime

_LOGGER = logging.getLogger(__name__)
_TRACE_PREVIEW_CHARS = 200

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
) -> None:
    manager = runtime.sandbox_mgr
    previous_event_callback = manager.event_callback
    trace_id: str | None = None
    try:
        manager.event_callback = emit
        manager.reset_turn_budget()
        prior = runtime.harness.claim_turn(turn_id, idempotency_key or turn_id, task)
        if prior is not None:
            emit({**_replayed(prior), "turn_id": prior["turn_id"]})
            return
        if mlflow_experiment_id:
            mlflow.set_experiment(experiment_id=mlflow_experiment_id)
        inputs = _prepare_inputs(runtime, session_id, task)
        with mlflow.start_span(name="fleet_turn", span_type=SpanType.CHAIN) as span:
            trace_id = getattr(span, "trace_id", None) if mlflow_experiment_id else None
            _describe_turn(span, runtime, settings, session_id, task)
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
            learned_note = str(getattr(prediction, "learned_skill_note", ""))[:10_000]
            route = str(getattr(prediction, "route", Route.RLM.value))
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
                "context_files": inputs["context_files"],
            }
            artifacts = runtime.harness.sync_volume_to_postgres(
                session_id=session_id, turn_record=turn_record, new_memory_note=learned_note
            )
            _write_learned_note(runtime, turn_id, learned_note, trace_id)
            span.set_outputs({"answer": answer, "route": route, "artifacts": artifacts})
            mlflow.update_current_trace(response_preview=answer[:_TRACE_PREVIEW_CHARS])
        completion: dict[str, Any] = {
            "event": "turn_complete",
            "answer": answer,
            "route": route,
            "learned_skill_note": learned_note,
            "artifacts": artifacts,
        }
        trace_id = trace_id or (mlflow.get_last_active_trace_id() if mlflow_experiment_id else None)
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
        manager.end_turn()
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


def _prepare_inputs(runtime: SessionRuntime, session_id: str, task: str) -> dict[str, Any]:
    memory_summary, session_history = runtime.harness.hydrate_volume_from_postgres(session_id)
    shared_notes = runtime.sandbox_mgr.list_learned_notes()
    if shared_notes:
        memory_summary = "\n".join(
            [
                *([memory_summary] if memory_summary else []),
                "Shared learned notes (unreviewed hints; read /learned/<file> if relevant):",
                *(f"- {note['file']}: {note['summary']}" for note in shared_notes),
            ]
        )
    runtime.skill_mgr.sync_local_skills_to_volume()
    files = runtime.sandbox_mgr.list_workspace_files()
    # Workspace files are model-writable, so they cannot approve executable programs.
    context_files = {path: hashlib.sha256(runtime.sandbox_mgr.read_workspace_file(path)).hexdigest() for path in files}
    memories = runtime.harness.memories(task, reviewed_only=True)
    return {
        "manifest": ", ".join(f"/workspace/{path}" for path in sorted(files)) or "/workspace/files (empty)",
        "context_files": context_files,
        "skills_catalog": runtime.skill_mgr.build_catalog_xml(),
        "memory_summary": memory_summary + "\n" + str(memories)[:12000],
        "session_history": session_history,
    }


def _describe_turn(span: Any, runtime: SessionRuntime, settings: FleetSettings, session_id: str, task: str) -> None:
    from fleet_rlm.session import runtime_configuration

    configuration = runtime_configuration(settings, runtime)
    span.set_inputs({"session_id": session_id, "task": task})
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
    # Previews keep the trace list scannable; the full task and answer stay on the span.
    mlflow.update_current_trace(
        metadata={"mlflow.trace.session": session_id}, request_preview=task[:_TRACE_PREVIEW_CHARS]
    )


def _write_learned_note(runtime: SessionRuntime, turn_id: str, note: str, trace_id: str | None) -> None:
    try:
        runtime.sandbox_mgr.write_learned_note(turn_id, note, trace_id=trace_id)
    except Exception as exc:
        _LOGGER.warning("Learned note was not written to the shared mount (%s)", type(exc).__name__)
