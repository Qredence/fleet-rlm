"""One Turn, end to end: claim, prepare inputs, execute the engine, persist, and report.

Runs on a worker thread. Every outcome is reported through `emit` as a plain event dict, ending
with ``{"event": "done"}``; the caller decides how to deliver them (the API streams them as SSE).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

import dspy
import mlflow
from mlflow.entities import SpanType
from mlflow.entities.trace_location import MlflowExperimentLocation

from fleet_rlm.config import resolve_turn_deadline
from fleet_rlm.contracts import RequiredSkill, Route, SkillAdmissionError
from fleet_rlm.engine.turn_commit import (
    _CLEANUP_ATTRIBUTE,
    _artifact_changes,
    _capture_artifact_view,
    _failure_message,
    _replayed,
    _stop_terminal_turn,
    _terminal_failure,
    _write_learned_note,
)
from fleet_rlm.engine.turn_prepare import (
    _MAX_MEMORIES_CHARS,
    _ROOT_SANDBOX_ATTRIBUTE,
    _TRACE_PREVIEW_CHARS,
    _capture_replay_snapshot,
    _describe_turn,
    _format_reviewed_memories,
    _join_memory_sections,
    _prepare_empty_direct_inputs,
    _prepare_inputs,
    _provider_bounds_attribute,
    _turn_environment,
    _unpack_context,
)
from fleet_rlm.observability import associate_turn_agent, phase_span, stream_callbacks
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES, encode_snapshot
from fleet_rlm.sandbox.manager import CLEANUP_NOT_REQUIRED
from fleet_rlm.skills import validate_requested_skills
from fleet_rlm.store import IdempotencyConflictError

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntime

__all__ = [
    "MAX_REPLAY_FILE_BYTES",
    "_CLEANUP_ATTRIBUTE",
    "_MAX_MEMORIES_CHARS",
    "_ROOT_SANDBOX_ATTRIBUTE",
    "Emit",
    "_artifact_changes",
    "_capture_artifact_view",
    "_describe_turn",
    "_failure_message",
    "_format_reviewed_memories",
    "_join_memory_sections",
    "_prepare_inputs",
    "_replayed",
    "_stop_terminal_turn",
    "_terminal_failure",
    "_unpack_context",
    "_write_learned_note",
    "run_turn",
]

_LOGGER = logging.getLogger(__name__)
Emit = Callable[[dict[str, Any]], None]


def _validate_skill_intent(
    runtime: SessionRuntime, requested_skills: tuple[str, ...]
) -> tuple[tuple[RequiredSkill, ...], SkillAdmissionError | None, dict[str, Any]]:
    if not requested_skills:
        return (), None, {}
    required_skills: tuple[RequiredSkill, ...] = ()
    admission_error: SkillAdmissionError | None = None
    try:
        validate_requested_skills(requested_skills)
        if runtime.engine.execution_mode == "direct":
            raise SkillAdmissionError("Direct mode cannot use required Skills; select Hybrid, RLM or Flex")
        required_skills = runtime.skill_mgr.resolve_required(requested_skills)
    except SkillAdmissionError as exc:
        admission_error = exc
    skill_intent = {
        "requested_skills": list(requested_skills),
        "required_skills": [
            {"name": skill.name, "source": skill.source, "sha256": skill.sha256} for skill in required_skills
        ],
    }
    return required_skills, admission_error, skill_intent


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
    record_replay: bool = True,
    requested_skills: tuple[str, ...] = (),
    timeout_seconds: int | None = None,
    completion_committed: Event | None = None,
    completion_lock: Lock | None = None,
    cancellation_requested: Event | None = None,
) -> None:
    """Run one Turn for a Session, claiming its id and emitting progress events through emit."""
    manager = runtime.sandbox_mgr
    started_at = time.monotonic()
    policy = resolve_turn_deadline(
        timeout_seconds,
        settings.turn_timeout_seconds,
        configured_source=settings.configuration_source("turn_timeout_seconds"),
    )
    previous_event_callback = manager.event_callback
    previous_snapshot_callback: Any = getattr(manager, "snapshot_resolved_callback", None)
    trace_id: str | None = None
    committed: dict[str, Any] | None = None
    commit_attempted = False
    terminal_event: dict[str, Any] | None = None
    turn_ended = False
    required_skills: tuple[RequiredSkill, ...] = ()
    admission_error: SkillAdmissionError | None = None
    skill_intent: dict[str, Any] = {}

    def end_execution() -> None:
        """End the execution deadline once, before observing or delivering committed success."""
        nonlocal turn_ended
        if not turn_ended:
            turn_ended = True
            manager.end_turn()

    def resolve_exception(exc: Exception) -> None:
        """Fence both span scopes with the same durable outcome and bounded readback."""
        nonlocal committed, terminal_event
        if isinstance(exc, IdempotencyConflictError):
            terminal_event = {
                "event": "error",
                "turn_id": turn_id,
                "request_id": turn_id,
                "error": str(exc),
                "error_type": "idempotency_conflict",
            }
            return
        uncertain = False
        if committed is None and terminal_event is None and commit_attempted:
            try:
                prior = runtime.store.turn_status(session_id, turn_id)
            except Exception:
                uncertain = True
            else:
                if prior is None or prior["turn_id"] != turn_id:
                    uncertain = True
                elif prior["status"] == "completed":
                    committed = _replayed(prior)
                    if completion_committed is not None:
                        completion_committed.set()
                    end_execution()
        if committed is not None or terminal_event is not None:
            _LOGGER.warning("Turn %s observation failed (%s)", turn_id, type(exc).__name__)
            return
        terminal_event = _terminal_failure(
            runtime,
            manager,
            session_id=session_id,
            turn_id=turn_id,
            exc=exc,
            trace_id=trace_id,
            started_at=started_at,
            persist=not uncertain,
            skill_intent=skill_intent,
        )
        if uncertain:
            terminal_event["error"] = "Prior turn has an uncertain or failed outcome; it was not replayed."

    try:
        runtime.assert_lease_held()
        manager.event_callback = emit
        with completion_lock if completion_lock is not None else nullcontext():
            prior = (
                runtime.completed_turn(idempotency_key or turn_id, task, requested_skills) if requested_skills else None
            )
            if prior is not None:
                committed = {**_replayed(prior), "turn_id": prior["turn_id"]}
                if completion_committed is not None:
                    completion_committed.set()
                return
            required_skills, admission_error, skill_intent = _validate_skill_intent(runtime, requested_skills)
            if requested_skills:
                prior = runtime.claim_turn(
                    turn_id,
                    idempotency_key or turn_id,
                    task,
                    requested_skills=requested_skills,
                    required_skills=required_skills,
                    admission_error=admission_error,
                )
            else:
                prior = runtime.claim_turn(turn_id, idempotency_key or turn_id, task)
            if prior is not None:
                terminal_event = {**_replayed(prior), "turn_id": prior["turn_id"]}
                if prior["status"] == "completed":
                    committed, terminal_event = terminal_event, None
                    if completion_committed is not None:
                        completion_committed.set()
                return
            manager.reset_turn_budget(deadline_seconds=policy.effective_seconds, deadline_source=policy.source)
            runtime.assert_lease_held()
            if cancellation_requested is not None and cancellation_requested.is_set():
                manager.request_cancellation()
                raise RuntimeError("Turn cancelled")
        if admission_error is not None:
            raise admission_error
        destination = MlflowExperimentLocation(mlflow_experiment_id) if mlflow_experiment_id else None
        with mlflow.start_span(name="fleet_turn", span_type=SpanType.CHAIN, trace_destination=destination) as span:
            try:
                trace_id = getattr(span, "trace_id", None) if mlflow_experiment_id else None
                span.set_inputs({"session_id": session_id, "task": task, "turn_id": turn_id, **skill_intent})
                span.set_attributes(manager.deadline_provenance())

                def record_snapshot_identity(identity: Any) -> None:
                    span.set_attributes(identity.provenance())

                manager.snapshot_resolved_callback = record_snapshot_identity
                resolved_identity = getattr(manager, "snapshot_identity", None)
                if resolved_identity is not None:
                    record_snapshot_identity(resolved_identity)
                selected_route: Route | None = None
                with phase_span("turn.prepare_inputs", session_id=session_id, record_replay=record_replay) as phase:
                    inputs, selected_route = _prepare_empty_direct_inputs(
                        runtime,
                        session_id,
                        task,
                        callback=callback,
                        record_replay=record_replay,
                        required_skills=required_skills,
                        dspy_module=dspy,
                    )
                    if inputs is None:
                        inputs = _prepare_inputs(
                            runtime, session_id, task, record_replay=record_replay, required_skills=required_skills
                        )
                    if inputs["replay_skipped_bytes"] is not None:
                        emit(
                            {
                                "event": "replay_skipped",
                                "bytes": inputs["replay_skipped_bytes"],
                                "limit": MAX_REPLAY_FILE_BYTES,
                            }
                        )
                    phase.set_outputs(
                        {
                            "context_files": len(inputs["context_files"]),
                            "task_chars": len(task),
                            "workspace_manifest_chars": len(inputs["manifest"]),
                            "dataset_preview_chars": len(runtime.context.rlm_preview()) if runtime.context else 0,
                            "skills_catalog_chars": len(inputs["skills_catalog"]),
                            "memory_summary_chars": len(inputs["memory_summary"]),
                            "session_history_chars": len(inputs["session_history"]),
                            "replay_files": len(inputs["replay_files"]),
                        }
                    )
                snapshot_provenance = manager.snapshot_provenance()
                replay_snapshot = (
                    _capture_replay_snapshot(
                        runtime,
                        settings,
                        session_id=session_id,
                        turn_id=turn_id,
                        task=task,
                        inputs=inputs,
                        policy=policy,
                        snapshot_provenance=snapshot_provenance,
                        requested_skills=requested_skills,
                        skill_intent=skill_intent,
                        emit=emit,
                        encoder=encode_snapshot,
                    )
                    if inputs["replay_selected"]
                    else None
                )
                _describe_turn(
                    span,
                    runtime,
                    settings,
                    session_id,
                    task,
                    turn_id,
                    idempotency_key,
                    inputs,
                    replay_snapshot,
                    skill_intent=skill_intent,
                )
                with dspy.context(callbacks=stream_callbacks(callback), disable_history=True):
                    execution_options: dict[str, Any] = {}
                    if selected_route is not None:
                        execution_options["selected_route"] = selected_route
                    if required_skills:
                        execution_options["required_skills"] = required_skills
                    if inputs.get("rlm_history") is not None:
                        execution_options["rlm_history"] = inputs["rlm_history"]
                    prediction = runtime.engine(
                        workspace_manifest=inputs["manifest"],
                        skills_catalog=inputs["skills_catalog"],
                        memory_summary=inputs["memory_summary"],
                        session_history=inputs["session_history"],
                        task=task,
                        execution_mode=runtime.engine.execution_mode,
                        context=runtime.context,
                        **execution_options,
                    )
                answer = str(prediction.answer)
                usage = {
                    name: getattr(prediction, name, None)
                    for name in ("lm_calls", "sandbox_errors", "submit_rejections", "budget_breakdown")
                }
                direct_context = {
                    name: getattr(prediction, name, None)
                    for name in ("direct_context_chars", "direct_context_truncated")
                    if getattr(prediction, name, None) is not None
                }
                learned_note = str(getattr(prediction, "learned_skill_note", ""))[:10_000]
                route = str(getattr(prediction, "route", Route.RLM.value))
                evidence = deepcopy(getattr(prediction, "execution_evidence", {}))
                if not evidence and callable(getattr(manager, "execution_evidence", None)):
                    evidence = deepcopy(manager.execution_evidence())
                artifact_capture = _capture_artifact_view(evidence)
                artifact_changes = _artifact_changes(inputs["context_files"], artifact_capture)
                runtime.assert_lease_held()
                environment = _turn_environment(manager)
                provider_bounds_attribute = _provider_bounds_attribute(environment)
                if provider_bounds_attribute is not None:
                    span.set_attribute("provider_bounds", provider_bounds_attribute)
                duration_seconds = round(time.monotonic() - started_at, 3)
                turn_record = {
                    **skill_intent,
                    "task": task,
                    "workspace_manifest": inputs["manifest"],
                    "skills_catalog": inputs["skills_catalog"],
                    "answer": answer,
                    "route": route,
                    "outcome": "completed",
                    "duration_seconds": duration_seconds,
                    "learned_skill_note": learned_note,
                    "turn_id": turn_id,
                    "idempotency_key": idempotency_key,
                    "execution_mode": runtime.engine.execution_mode,
                    "trace_id": trace_id,
                    **usage,
                    **direct_context,
                    **environment,
                    "context_files": inputs["context_files"],
                    "artifact_capture": artifact_capture,
                    "artifact_changes": artifact_changes,
                    "replay_snapshot_selected": inputs["replay_selected"],
                    "replay_snapshot_sha256": replay_snapshot[0] if replay_snapshot else None,
                    **(
                        {"rlm_history": deepcopy(getattr(prediction, "rlm_history", None))}
                        if getattr(prediction, "rlm_history", None)
                        else {}
                    ),
                    _CLEANUP_ATTRIBUTE: CLEANUP_NOT_REQUIRED,
                }
                with phase_span("turn.commit", turn_id=turn_id, learned_note=bool(learned_note)) as phase:
                    with completion_lock if completion_lock is not None else nullcontext():
                        if manager._cancelled.is_set():
                            raise RuntimeError("Turn was cancelled before commit")
                        runtime.assert_lease_held()
                        commit_attempted = True
                        try:
                            committed = runtime.commit_turn(
                                session_id=session_id,
                                turn_record=turn_record,
                                new_memory_note=learned_note,
                                replay_snapshot=replay_snapshot,
                                artifact_paths=artifact_capture["paths"],
                            )
                        except Exception as exc:
                            resolve_exception(exc)
                            raise
                        if completion_committed is not None:
                            completion_committed.set()
                        end_execution()
                    phase.set_outputs({"artifacts": len(committed["artifacts"])})
                _write_learned_note(runtime, turn_id, learned_note, trace_id)
                span.set_attributes(
                    {"turn_id": turn_id, "route": route, "outcome": "completed", "duration_seconds": duration_seconds}
                )
                span.set_outputs(
                    {
                        "turn_id": turn_id,
                        "answer": answer,
                        "route": route,
                        "outcome": "completed",
                        "duration_seconds": duration_seconds,
                        **usage,
                        _CLEANUP_ATTRIBUTE: CLEANUP_NOT_REQUIRED,
                        "execution_evidence": evidence,
                        "artifacts": deepcopy(committed["artifacts"]),
                        "artifact_changes": artifact_changes,
                    }
                )
                span.set_attribute(
                    "compiled_program_digest", getattr(getattr(runtime.engine, "compiled", None), "digest", "")
                )
                mlflow.update_current_trace(response_preview=answer[:_TRACE_PREVIEW_CHARS], tags={"fleet.route": route})
            except Exception as exc:
                resolve_exception(exc)
                if committed is not None:
                    return
                if terminal_event is None:
                    raise RuntimeError("Turn exception resolution produced no terminal event") from exc
                span.set_status("ERROR")
                span.set_outputs(terminal_event)
                span.set_attributes(
                    {
                        "turn_id": turn_id,
                        "outcome": terminal_event["outcome"],
                        "duration_seconds": terminal_event["duration_seconds"],
                    }
                )
                span.set_attribute(_CLEANUP_ATTRIBUTE, terminal_event[_CLEANUP_ATTRIBUTE])
                failure_bounds = _provider_bounds_attribute(terminal_event)
                if failure_bounds is not None:
                    span.set_attribute("provider_bounds", failure_bounds)
                if terminal_event.get(_ROOT_SANDBOX_ATTRIBUTE):
                    span.set_attribute(_ROOT_SANDBOX_ATTRIBUTE, terminal_event[_ROOT_SANDBOX_ATTRIBUTE])
            else:
                span.set_attribute(_CLEANUP_ATTRIBUTE, CLEANUP_NOT_REQUIRED)
            finally:
                from fleet_rlm.session import runtime_configuration

                try:
                    associate_turn_agent(
                        runtime.engine.program,
                        runtime_configuration(settings, runtime)["digest"],
                        mlflow_experiment_id,
                        span,
                    )
                except Exception as exc:
                    _LOGGER.warning("Turn identity configuration unavailable (%s)", type(exc).__name__)
    except Exception as exc:
        resolve_exception(exc)
    finally:
        try:
            end_execution()
        finally:
            manager.event_callback = previous_event_callback
            manager.snapshot_resolved_callback = previous_snapshot_callback
            try:
                terminal = committed if committed is not None else terminal_event
                if terminal is not None:
                    try:
                        emit(deepcopy(terminal))
                    except Exception as exc:
                        _LOGGER.warning("Turn %s delivery failed (%s)", turn_id, type(exc).__name__)
            finally:
                emit({"event": "done"})
