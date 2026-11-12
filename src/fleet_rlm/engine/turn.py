"""One Turn, end to end: claim, prepare the inputs, run the engine, persist, and report.

Runs on a worker thread. Every outcome is reported through `emit` as a plain event dict, ending
with ``{"event": "done"}``; the caller decides how to deliver them (the API streams them as SSE).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time
from collections.abc import Callable
from contextlib import nullcontext, suppress
from copy import deepcopy
from importlib.metadata import version
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

import dspy
import mlflow
from dspy.utils.exceptions import AdapterParseError
from mlflow.entities import SpanType
from mlflow.entities.trace_location import MlflowExperimentLocation

from fleet_rlm.config import resolve_turn_deadline
from fleet_rlm.contracts import RequiredSkill, Route, SkillAdmissionError
from fleet_rlm.observability import associate_turn_agent, phase_span, stream_callbacks
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES, encode_snapshot
from fleet_rlm.sandbox.interpreter import StructuredResponseError
from fleet_rlm.sandbox.manager import (
    CLEANUP_NOT_REQUIRED,
    CLEANUP_STOPPED,
    CLEANUP_UNCONFIRMED,
    SANDBOX_TRANSPORT,
)
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.sandbox.snapshots import MAX_REFUSAL_CHARS, SnapshotUnavailableError
from fleet_rlm.skills import validate_requested_skills
from fleet_rlm.store import IdempotencyConflictError

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntime

_LOGGER = logging.getLogger(__name__)
_TRACE_PREVIEW_CHARS = 200
# The field that carries the teardown result on the stored Turn, its span, and the terminal event.
_CLEANUP_ATTRIBUTE = "cleanup"
# The live root provider handle id, recorded only when this Turn actually provisioned one.
_ROOT_SANDBOX_ATTRIBUTE = "root_sandbox_id"
# Provider-owned lifecycle bounds, surfaced unchanged and distinct from the Fleet deadline.
_PROVIDER_BOUND_KEYS = ("ttl_minutes", "auto_stop_interval", "maximum_lifespan_minutes", "provider_owned")


def _provider_bounds(manager: Any) -> dict[str, Any]:
    """Read the provider-owned bounds from the manager, or nothing when it cannot report them."""
    reader = getattr(manager, "provider_bounds", None)
    bounds = reader() if callable(reader) else {}
    return bounds if isinstance(bounds, dict) else {}


def _provider_bounds_attribute(bounds: dict[str, Any]) -> str | None:
    """Serialize only the provider-bound fields for one bounded span attribute."""
    values = {key: bounds[key] for key in _PROVIDER_BOUND_KEYS if key in bounds}
    return json.dumps(values, sort_keys=True) if values else None


def _live_sandbox_id(manager: Any) -> str | None:
    """Return the root provider handle id, or None when nothing was provisioned."""
    reader = getattr(manager, "live_sandbox_id", None)
    sandbox_id = reader() if callable(reader) else reader
    return sandbox_id if isinstance(sandbox_id, str) and sandbox_id else None


def _turn_environment(manager: Any) -> dict[str, Any]:
    """Snapshot, deadline, root sandbox id, and provider bounds for a Turn outcome."""
    root_sandbox_id = _live_sandbox_id(manager)
    return {
        **manager.snapshot_provenance(),
        **manager.deadline_provenance(),
        **({_ROOT_SANDBOX_ATTRIBUTE: root_sandbox_id} if root_sandbox_id else {}),
        **_provider_bounds(manager),
    }


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
    record_replay: bool = True,
    requested_skills: tuple[str, ...] = (),
    timeout_seconds: int | None = None,
    completion_committed: Event | None = None,
    completion_lock: Lock | None = None,
    cancellation_requested: Event | None = None,
) -> None:
    """Run one Turn for a Session, claiming its id and emitting progress events through emit.

    The deadline policy is resolved once, before any work starts: the request override
    wins over configuration, which wins over the shipped default. `None` inherits and
    `0` disables the Fleet deadline for this Turn only.

    An existing idempotency claim replays its stored outcome. New work persists its
    completion and optionally captures immutable replay inputs. Ordinary failures
    become terminal events after teardown; success keeps the root sandbox warm.
    Observation and delivery errors after commit cannot reverse the durable outcome.
    `completion_committed` lets the transport preserve success across a disconnect
    before the terminal event can be queued. `completion_lock` serializes admission,
    that signal and commit/readback with disconnect cancellation. Requests cancelled
    during source admission cannot start execution. The execution deadline starts only
    for a newly claimed Turn, not receipt lookup or source comparison. Finalization errors may propagate.
    """
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
            # No work was admitted and no new row was claimed. Do not stop a prior warm root.
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
            # Leave intent intact: a later idempotency lookup may prove completion. Never
            # overwrite an unknown transaction outcome or rerun the generated code.
            terminal_event["error"] = "Prior turn has an uncertain or failed outcome; it was not replayed."

    try:
        runtime.assert_lease_held()
        manager.event_callback = emit
        with completion_lock if completion_lock is not None else nullcontext():
            # Replay and source comparison own no live execution. A disconnect waits for
            # this decision, recording its intent without stopping a prior warm root.
            prior = (
                runtime.completed_turn(idempotency_key or turn_id, task, requested_skills) if requested_skills else None
            )
            if prior is not None:
                committed = {**_replayed(prior), "turn_id": prior["turn_id"]}
                if completion_committed is not None:
                    completion_committed.set()
                return
            if requested_skills:
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
                        {"name": skill.name, "source": skill.source, "sha256": skill.sha256}
                        for skill in required_skills
                    ],
                }
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
            manager.reset_turn_budget(
                deadline_seconds=policy.effective_seconds,
                deadline_source=policy.source,
            )
            # Source admission may outlive a lease probe; reset must not revive a lost owner.
            runtime.assert_lease_held()
            if cancellation_requested is not None and cancellation_requested.is_set():
                manager.request_cancellation()
                raise RuntimeError("Turn cancelled")
        if admission_error is not None:
            raise admission_error
        # The destination travels with the span: `mlflow.set_experiment` is process-wide and would race
        # across Sessions that trace to different experiments.
        destination = MlflowExperimentLocation(mlflow_experiment_id) if mlflow_experiment_id else None
        with mlflow.start_span(name="fleet_turn", span_type=SpanType.CHAIN, trace_destination=destination) as span:
            try:
                trace_id = getattr(span, "trace_id", None) if mlflow_experiment_id else None
                # Keep enough context on the trace if preparation fails. The complete prepared inputs
                # replace this small initial payload once they are available.
                span.set_inputs({"session_id": session_id, "task": task, "turn_id": turn_id, **skill_intent})
                # Resolved once above; a failure anywhere after this point still reports it.
                span.set_attributes(manager.deadline_provenance())

                def record_snapshot_identity(identity: Any) -> None:
                    """Record the resolved provider snapshot provenance on the active Turn span."""
                    span.set_attributes(identity.provenance())

                # Record the identity the instant preflight resolves it. A later preparation step
                # (replay capture, oversized payload, engine setup) may still raise, and the failure
                # path must report the same provenance as the persisted Turn.
                manager.snapshot_resolved_callback = record_snapshot_identity
                resolved_identity = getattr(manager, "snapshot_identity", None)
                if resolved_identity is not None:
                    record_snapshot_identity(resolved_identity)
                selected_route: Route | None = None
                with phase_span("turn.prepare_inputs", session_id=session_id, record_replay=record_replay) as phase:
                    inputs: dict[str, Any] | None = None
                    can_route_empty = (
                        runtime.workspace_state == "known_empty"
                        and runtime.context is None
                        and not required_skills
                        and callable(getattr(runtime.engine, "preview_route", None))
                    )
                    if can_route_empty:
                        local_files: dict[str, bytes] = {}
                        try:
                            if callable(stage_local := getattr(runtime.skill_mgr, "stage_local_skills", None)):
                                local_catalog, local_files = stage_local()
                            else:
                                local_catalog = runtime.skill_mgr.build_local_catalog_xml()
                                local_files = (
                                    local_files_fn()
                                    if callable(local_files_fn := getattr(runtime.skill_mgr, "local_skill_files", None))
                                    else {}
                                )
                        except (OSError, UnicodeError, ValueError):
                            local_catalog = None
                        if local_catalog is not None:
                            with phase_span("turn.skill_catalog", source="local") as skill_phase:
                                skill_phase.set_outputs({"chars": len(local_catalog)})
                            empty_manifest = (
                                ", ".join(f"/workspace/{path}" for path in sorted(local_files))
                                or "/workspace/files (empty)"
                            )
                            with dspy.context(callbacks=stream_callbacks(callback), disable_history=True):
                                selected_route = runtime.engine.preview_route(
                                    task=task, workspace_manifest=empty_manifest, skills_catalog=local_catalog
                                )
                            if selected_route is Route.DIRECT:
                                with phase_span("turn.load_context", session_id=session_id) as context_phase:
                                    memory_summary, session_history = runtime.load_context(session_id)
                                    context_phase.set_outputs(
                                        {"memory_chars": len(memory_summary), "history_chars": len(session_history)}
                                    )
                                with phase_span("turn.reviewed_memories", task_chars=len(task)) as memory_phase:
                                    memories = runtime.memories(task, reviewed_only=True)
                                    memory_phase.set_outputs({"memories": len(memories)})
                                replay_files: dict[str, str] = {}
                                replay_selected = False
                                replay_skipped_bytes: int | None = None
                                if record_replay:
                                    total_bytes = sum(map(len, local_files.values()))
                                    if total_bytes <= MAX_REPLAY_FILE_BYTES:
                                        replay_files = {
                                            path: base64.b64encode(content).decode("ascii")
                                            for path, content in sorted(local_files.items())
                                        }
                                        replay_selected = True
                                    else:
                                        replay_skipped_bytes = total_bytes
                                inputs = {
                                    "manifest": empty_manifest,
                                    "context_files": {
                                        path: hashlib.sha256(content).hexdigest()
                                        for path, content in sorted(local_files.items())
                                    },
                                    "skills_catalog": local_catalog,
                                    "memory_summary": memory_summary + "\n" + str(memories)[:12000],
                                    "session_history": session_history,
                                    "replay_files": replay_files,
                                    "replay_selected": replay_selected,
                                    "replay_skipped_bytes": replay_skipped_bytes,
                                }
                            else:
                                # Any execution route may write files, so future Turns must inspect the Volume.
                                runtime.set_workspace_state("unknown")
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
                    # Counts only: the prepared text is already on the root span's inputs.
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
                # The provider resolved this identity during preparation; every surface records
                # these exact values rather than re-deriving them from the definition.
                snapshot_provenance = manager.snapshot_provenance()
                replay_snapshot: tuple[str, bytes] | None = None
                if inputs["replay_selected"]:
                    from fleet_rlm.session import runtime_configuration

                    with phase_span("turn.capture_replay", turn_id=turn_id) as phase:
                        snapshot = {
                            "schema_version": 2,
                            "requested_skills": list(requested_skills),
                            "required_skills": skill_intent.get("required_skills", []),
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
                            "dataset_context": (
                                runtime.context.to_descriptor() if runtime.context is not None else None
                            ),
                            "execution_mode": runtime.engine.execution_mode,
                            "turn_timeout_seconds": manager.turn_timeout_seconds,
                            "deadline_source": policy.source,
                            "effective_seconds": policy.effective_seconds,
                            "sandbox_transport": SANDBOX_TRANSPORT,
                            **snapshot_provenance,
                            "configuration": runtime_configuration(settings, runtime),
                            "provenance": {"fleet_rlm_version": version("fleet-rlm")},
                        }
                        from fleet_rlm.replay import ReplaySizeError

                        try:
                            replay_snapshot = encode_snapshot(snapshot)
                        except ReplaySizeError as error:
                            inputs["replay_selected"] = False
                            emit({"event": "replay_skipped", "bytes": error.size, "limit": error.limit})
                        phase.set_attributes(
                            {
                                "snapshot_id": replay_snapshot[0] if replay_snapshot else None,
                                "snapshot_bytes": len(replay_snapshot[1]) if replay_snapshot else 0,
                                "replay_files": len(inputs["replay_files"]),
                            }
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
                # Direct mode inlines the workspace into its prompt; keep that measurable per Turn
                # rather than inferring it from the route.
                direct_context = {
                    name: getattr(prediction, name, None)
                    for name in ("direct_context_chars", "direct_context_truncated")
                    if getattr(prediction, name, None) is not None
                }
                learned_note = str(getattr(prediction, "learned_skill_note", ""))[:10_000]
                route = str(getattr(prediction, "route", Route.RLM.value))
                evidence = deepcopy(getattr(prediction, "execution_evidence", {}))
                if not evidence and callable(getattr(manager, "execution_evidence", None)):
                    # Alternate engines may omit evidence; collect one host view at the same
                    # completion boundary instead of letting Session relist later.
                    evidence = deepcopy(manager.execution_evidence())
                artifact_capture = _capture_artifact_view(evidence)
                artifact_changes = _artifact_changes(inputs["context_files"], artifact_capture)
                runtime.assert_lease_held()
                # The successful Turn deliberately keeps its warm root, so this is the handle the
                # next Turn will reuse; record it and provider bounds alongside provenance.
                environment = _turn_environment(manager)
                provider_bounds_attribute = _provider_bounds_attribute(environment)
                if provider_bounds_attribute is not None:
                    span.set_attribute("provider_bounds", provider_bounds_attribute)
                # One wall duration for every surface; the span attribute and the stored record are
                # joined within the documented two-second tolerance of MLflow's own span duration.
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
                    # A completed Turn keeps its warm root sandbox for the next Turn.
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
                # The span carries the same join keys as the record, status payload, and terminal
                # event so one turn_id resolves all four surfaces without re-deriving anything.
                span.set_attributes(
                    {
                        "turn_id": turn_id,
                        "route": route,
                        "outcome": "completed",
                        "duration_seconds": duration_seconds,
                    }
                )
                span.set_outputs(
                    {
                        "turn_id": turn_id,
                        "answer": answer,
                        "route": route,
                        "outcome": "completed",
                        "duration_seconds": duration_seconds,
                        # Cheap scalar facts go before the evidence tree: MLflow sanitizes a span
                        # with a fixed node budget, and an artifact-heavy capture would otherwise
                        # truncate the route, usage, and cleanup facts recorded after it.
                        **usage,
                        _CLEANUP_ATTRIBUTE: CLEANUP_NOT_REQUIRED,
                        "execution_evidence": evidence,
                        "artifacts": deepcopy(committed["artifacts"]),
                        "artifact_changes": artifact_changes,
                    }
                )
                # Read after the engine ran: it reloads an approved program at the start of the Turn.
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
                # Stay inside the root span on success, preparation failure, and observation failure.
                # The loaded state can differ from the artifact currently on disk.
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
            # Teardown precedes acknowledgement: the terminal event and `done` follow this call.
            end_execution()
        finally:
            # The stream's end marker must survive a failing sandbox cleanup.
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


def _stop_terminal_turn(manager: Any) -> str:
    """Stop a terminal Turn's sandbox work once and return the cleanup outcome it recorded.

    The manager's cancellation guard makes this Turn's teardown single-owner: when a deadline,
    transport failure, watchdog, or disconnect already claimed it, this call waits for that
    teardown instead of invoking `cancel_turn()` a second time for the same Turn.
    """
    try:
        manager.request_cancellation()
    except Exception:
        unconfirmed = True
    else:
        # A watchdog or disconnect may have run the teardown first and recorded its result.
        unconfirmed = getattr(manager, "cleanup_outcome", None) == CLEANUP_UNCONFIRMED
    if not unconfirmed:
        return CLEANUP_STOPPED
    # Bounded: a provider payload never reaches the client, the stored Turn, or the log.
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
    """Resolve one failed or cancelled outcome, tear the Turn down, and store it before acknowledgment.

    The cancellation classification is read before teardown because `cancel_turn()` sets
    `_cancelled` itself. A transport failure may already have requested teardown, but its
    unknown execution outcome remains a failure. Teardown runs once; a provider stop that
    raises is recorded as `unconfirmed` and never truncates the stream.
    `persist=False` performs the same cleanup while leaving an uncertain commit's
    durable intent untouched for later idempotency recovery.
    """
    cancelled = manager._cancelled.is_set() and not isinstance(exc, ExecutionOutcomeUnknownError)
    # Read the live handle and provider bounds before teardown discards the handle.
    environment = _turn_environment(manager)
    if persist:
        _LOGGER.error("Turn %s failed for Session %s (%s)", turn_id, session_id, type(exc).__name__)
    else:
        _LOGGER.error(
            "Turn %s commit outcome is uncertain for Session %s (%s)", turn_id, session_id, type(exc).__name__
        )
    outcome = "cancelled" if cancelled else "failed"
    cleanup = _stop_terminal_turn(manager)
    # Measured after teardown so the recorded wall duration covers everything before acknowledgment.
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
        with suppress(Exception):
            runtime.store.finish_turn(
                session_id,
                turn_id,
                outcome,
                failure,
                **({"ownership_epoch": runtime.ownership_epoch} if runtime.ownership_epoch is not None else {}),
            )
    return failure


def _failure_message(exc: BaseException, turn_id: str) -> str:
    """Return the only failure text a client or stored Turn sees.

    Snapshot preflight refusals are constructed by the repository's snapshot module
    from the snapshot name, observed provider state/resources, the required floors,
    and the build command, so those bounded details are explicitly safe to surface.
    Idempotency conflicts also retain their caller-safe message.
    Native and host-verified bridged format errors receive a fixed safe explanation.
    All remaining exceptions stay sanitized: their text can carry file paths,
    credentials, or provider payloads.
    """
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
    """Compare host digests; unchanged content is retained, not evidence of a new write.

    The existing artifacts list remains the Session inventory. Missing byte evidence cannot
    establish whether an existing file changed or was deleted.
    """
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
    # Historical complete captures used the file mapping as their inventory. A partial byte
    # capture needs the separately collected full path list from the same host observation.
    paths = observed.get("paths", sorted(files) if complete else None)
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
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
    # Promotion receipts describe an earlier host boundary. The root can legitimately
    # remove, move, or rewrite those outputs before the final workspace observation.
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


def _prepare_inputs(
    runtime: SessionRuntime,
    session_id: str,
    task: str,
    *,
    record_replay: bool = True,
    required_skills: tuple[RequiredSkill, ...] = (),
) -> dict[str, Any]:
    """Sync Skills and assemble workspace digests, memory, history, and the Skill catalog.

    With record_replay, also return base64-encoded workspace bytes in replay_files;
    otherwise that mapping is empty. A workspace over the replay byte limit skips
    capture: replay_selected is False and replay_skipped_bytes carries the byte count.
    Provisioning, storage, and workspace errors propagate.

    Raises:
        ValueError: A replay file changed after hashing.
    """
    with phase_span("turn.load_context", session_id=session_id) as phase:
        memory_summary, session_history = runtime.load_context(session_id)
        phase.set_outputs({"memory_chars": len(memory_summary), "history_chars": len(session_history)})
    # Sync can partially write even on failure; invalidate before touching the Volume.
    runtime.set_workspace_state("unknown")
    # Skills land first so their files are hashed; everything else remote arrives in one exec.
    with phase_span("turn.sync_skills") as phase:
        phase.set_outputs(
            {
                "skills": runtime.skill_mgr.sync_local_skills_to_volume(
                    **({"required_skills": required_skills} if required_skills else {})
                )
            }
        )
    with phase_span("turn.workspace_inputs") as phase:
        remote = runtime.sandbox_mgr.turn_inputs()
        phase.set_outputs(
            {
                "files": len(remote["digests"]),
                "has_environment": bool(remote["environment"]),
            }
        )
    # Workspace files are model-writable, so they cannot approve executable programs.
    context_files = remote["digests"]
    required_files = {
        f"skills/{skill.name}/{path}": content for skill in required_skills for path, content in skill.files
    }
    for skill in required_skills:
        prefix = f"skills/{skill.name}/"
        if {path for path in context_files if path.startswith(prefix)} != {
            path for path in required_files if path.startswith(prefix)
        }:
            raise SkillAdmissionError("Required Skill resource manifest changed during preparation")
    for path, content in required_files.items():
        if context_files.get(path) != hashlib.sha256(content).hexdigest():
            raise SkillAdmissionError("Required Skill files changed during preparation")
    files = sorted(context_files)
    replay_files: dict[str, str] = {}
    replay_bytes = 0
    replay_selected = record_replay
    replay_skipped_bytes: int | None = None
    # Only a selected replay needs the bytes; other Turns record the remotely computed digests.
    if record_replay:
        # Remote sizes decide before any download: an oversized workspace is never pulled just to be dropped.
        remote_sizes = remote.get("sizes", {})
        known_bytes = sum(len(required_files[p]) if p in required_files else remote_sizes.get(p, 0) for p in files)
        if known_bytes > MAX_REPLAY_FILE_BYTES:
            replay_selected, replay_skipped_bytes = False, known_bytes
        with phase_span("turn.replay_files") as phase:
            remote_paths = [path for path in files if path not in required_files] if replay_selected else []
            downloaded = runtime.sandbox_mgr.read_workspace_files(remote_paths) if remote_paths else {}
            for path in files if replay_selected else ():
                content = required_files[path] if path in required_files else downloaded[path]
                if hashlib.sha256(content).hexdigest() != context_files[path]:
                    raise ValueError(f"Workspace file changed while the Turn was prepared: {path}")
                replay_bytes += len(content)
                if replay_bytes > MAX_REPLAY_FILE_BYTES:
                    # Capture is best-effort: an oversized workspace skips the snapshot, not the Turn.
                    replay_files.clear()
                    replay_selected = False
                    replay_skipped_bytes = replay_bytes
                    break
                replay_files[path] = base64.b64encode(content).decode("ascii")
            phase.set_outputs({"files": len(replay_files), "bytes": replay_bytes})
    with phase_span("turn.reviewed_memories", task_chars=len(task)) as phase:
        memories = runtime.memories(task, reviewed_only=True)
        phase.set_outputs({"memories": len(memories)})
    with phase_span("turn.skill_catalog") as phase:
        skills_catalog = runtime.skill_mgr.build_catalog_xml()
        phase.set_outputs({"chars": len(skills_catalog)})
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
        "replay_selected": replay_selected,
        "replay_skipped_bytes": replay_skipped_bytes,
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
    *,
    skill_intent: dict[str, Any] | None = None,
) -> None:
    """Record prepared input references, configuration, and sandbox provenance on the Turn trace."""
    from fleet_rlm.session import runtime_configuration

    configuration = runtime_configuration(settings, runtime)
    # Preparation has provisioned (or reused) the root by now; the span records the same live
    # handle id as the stored Turn. A Turn that never provisioned records nothing.
    root_sandbox_id = _live_sandbox_id(runtime.sandbox_mgr)
    # The trace keeps a small integrity reference; opted-in file bytes stay in Lakebase.
    span.set_inputs(
        {
            "session_id": session_id,
            "task": task,
            "turn_id": turn_id,
            **(skill_intent or {}),
            "workspace_manifest": inputs["manifest"],
            "context_files": inputs["context_files"],
            "replay_snapshot_selected": replay_snapshot is not None,
            "replay_snapshot_sha256": replay_snapshot[0] if replay_snapshot else "",
        }
    )
    span.set_attributes(
        {
            "execution_mode": runtime.engine.execution_mode,
            "turn_timeout_seconds": runtime.sandbox_mgr.turn_timeout_seconds,
            **runtime.sandbox_mgr.deadline_provenance(),
            "sandbox_transport": SANDBOX_TRANSPORT,
            "configuration_digest": configuration["digest"],
            **runtime.sandbox_mgr.snapshot_provenance(),
            **({_ROOT_SANDBOX_ATTRIBUTE: root_sandbox_id} if root_sandbox_id else {}),
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
            "fleet.execution_mode": runtime.engine.execution_mode,
            "fleet.idempotency_key": idempotency_key or turn_id,
            "fleet.configuration_digest": configuration["digest"],
        },
        request_preview=task[:_TRACE_PREVIEW_CHARS],
    )


def _write_learned_note(runtime: SessionRuntime, turn_id: str, note: str, trace_id: str | None) -> None:
    """Write the Turn's learned note to the shared mount, suppressing ordinary write failures."""
    try:
        with phase_span("turn.write_learned_note", note_chars=len(note)) as phase:
            path = runtime.sandbox_mgr.write_learned_note(turn_id, note, trace_id=trace_id)
            phase.set_outputs({"written": bool(path)})
    except Exception as exc:
        _LOGGER.warning("Learned note was not written to the shared mount (%s)", type(exc).__name__)
