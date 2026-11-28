"""Chat turn admission, persistence, and SSE delivery."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from contextvars import Context
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

import dspy
import mlflow
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from mlflow.entities import LiveSpan
from mlflow.exceptions import MlflowException
from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .. import agent, chatgpt_plan, opt_runner, ui_stream
from .. import db as store
from ..config import Settings
from ..interpreter import (
    WORKSPACE,
    AsyncSandbox,
    DaytonaError,
    SandboxStartupError,
    delete_sandbox_by_id,
    download_workspace_file,
    list_new_artifacts,
    make_interpreter_factory,
    reconcile_managed_workspace,
    reset_managed_workspace,
    sandbox_cleanup_failed,
    upload_workspace_file,
)
from ..skills import SkillRegistry, load_host_tools, select_skills
from ..storage import ObjectStorage, StorageError
from ..tracing import active_span, capture, preview, record, without_tracing
from ..tracing import span as workspace_span
from .dependencies import _STREAM_HEADER, Runtime, RuntimeDep, _skill_registry
from .settings import _effective_settings, _interactive_models, _request_decision_lm
from .workspace import (
    _WORKSPACE_STARTUP_HELP,
    _ensure_bound_sandbox,
    _session,
    _try_object_cleanup,
    _valid_file_path,
)

router = APIRouter()
_LOG = logging.getLogger(__name__)


class MessagePart(BaseModel):
    type: str
    text: str | None = None


class Message(BaseModel):
    role: str
    parts: list[MessagePart]


class ChatPayload(BaseModel):
    messages: list[Message]
    pinned_skills: list[str] = Field(default_factory=list)

    def latest_prompt(self) -> str:
        for message in reversed(self.messages):
            if message.role == "user":
                prompt = "\n".join(
                    part.text for part in message.parts if part.type == "text" and part.text
                ).strip()
                if prompt:
                    return prompt
                break
        raise HTTPException(422, "The latest user message must contain text")


_CHAT_ADMISSION_SECONDS = 15


_MEMORY_ADMISSION_SECONDS = 1


_MEMORY_EXECUTION_SECONDS = 30


class _TurnPermit:
    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self.semaphore: asyncio.Semaphore | None = semaphore

    def release(self) -> None:
        if self.semaphore is not None:
            self.semaphore.release()
            self.semaphore = None


async def _wait_for_disconnect(request: Request) -> None:
    # FastAPI has consumed the request body. Stop this reader before the SSE response owns receive.
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _acquire_turn_permit(request: Request, semaphore: asyncio.Semaphore) -> _TurnPermit:
    slot = asyncio.create_task(semaphore.acquire())
    disconnect = asyncio.create_task(_wait_for_disconnect(request))
    transferred = False
    try:
        done, _ = await asyncio.wait(
            {slot, disconnect}, timeout=_CHAT_ADMISSION_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
        if disconnect in done:
            disconnect.result()
            raise HTTPException(499, "Client disconnected")
        if slot not in done:
            raise HTTPException(
                503, "Fleet is busy. Try again shortly.", headers={"Retry-After": "1"}
            )
        slot.result()
        disconnect.cancel()
        await asyncio.gather(disconnect, return_exceptions=True)
        if not disconnect.cancelled() or await request.is_disconnected():
            raise HTTPException(499, "Client disconnected")
        transferred = True
        return _TurnPermit(semaphore)
    finally:
        if not transferred:
            disconnect.cancel()
            slot.cancel()
            if slot.done() and not slot.cancelled() and slot.exception() is None:
                semaphore.release()
            await asyncio.gather(slot, disconnect, return_exceptions=True)


@dataclass
class Turn:
    run_id: UUID
    session_id: UUID
    prompt: str
    pinned_skills: list[str]
    skills: SkillRegistry
    deadline: float
    permit: _TurnPermit
    worker: asyncio.Task[list[dict]] | None = None
    finalizing: bool = False
    finalized: bool = False
    sandbox_for_cleanup: str | None = None


async def _finish_root(
    sessionmaker: store.SessionFactory,
    turn: Turn,
    status: store.TerminalStatus,
    *,
    child_snapshots: dict[str, list[dict]] | None = None,
    **result,
) -> bool:
    if status != "completed":
        running_children = (
            update(store.Run)
            .where(
                store.Run.root_run_id == turn.run_id,
                store.Run.parent_run_id.is_not(None),
                store.Run.status == "running",
            )
            .values(status=status, finished_at=func.now())
        )
        async with sessionmaker() as db, db.begin():
            for child_id, parts in (child_snapshots or {}).items():
                await db.execute(
                    running_children.where(store.Run.id == UUID(child_id)).values(
                        ui_parts=store.transcript_value(parts)
                    )
                )
            # A child-start transaction can commit before its interrupted callback returns.
            await db.execute(running_children)
    committed = await store.finish_run(
        sessionmaker,
        turn.run_id,
        status=status,
        retire_sandbox_id=turn.sandbox_for_cleanup,
        **result,
    )
    turn.finalized = True
    return committed


async def _prepare_turn(
    session_id: UUID,
    payload: ChatPayload,
    request: Request,
    response: Response,
    runtime: RuntimeDep,
) -> AsyncIterator[Turn]:
    prompt = payload.latest_prompt()
    registry = await _skill_registry(runtime)
    skills_by_name = registry.pin_names()
    mentioned = [
        name
        for name in skills_by_name
        if re.search(rf"(?<![\w@])@{re.escape(name)}(?![\w-])", prompt)
    ]
    pinned = list(
        dict.fromkeys([*(name.removeprefix("@") for name in payload.pinned_skills), *mentioned])
    )
    if set(pinned) - skills_by_name.keys():
        raise HTTPException(422, "Unknown pinned Skill")
    for name in pinned:
        if reason := skills_by_name[name].unavailable_reason(runtime.settings):
            raise HTTPException(422, reason)

    permit = await _acquire_turn_permit(request, runtime.turn_semaphore)
    try:
        deadline = (
            asyncio.get_running_loop().time() + runtime.settings.stale_run_timeout_seconds - 5
        )
        run = await _admit_root(request, runtime, session_id, prompt)
        turn = Turn(run.id, session_id, prompt, pinned, registry, deadline, permit)
        response.headers[_STREAM_HEADER] = "v1"
        try:
            yield turn
        finally:
            await _close_turn(runtime.sessionmaker, turn)
    finally:
        permit.release()


async def _admit_root(
    request: Request, runtime: Runtime, session_id: UUID, prompt: str
) -> store.Run:
    sessionmaker = runtime.sessionmaker
    await store.get_session_or_raise(sessionmaker, session_id)
    if await request.is_disconnected():
        raise HTTPException(499, "Client disconnected")
    try:
        try:
            return await store.acquire_root_run(sessionmaker, session_id, prompt)
        except store.UploadConflictError:
            raise
        except store.TurnConflictError as error:
            reaped = await store.reap_stale_runs(
                sessionmaker,
                runtime.settings.stale_run_timeout_seconds,
                session_id=session_id,
            )
            if reaped <= 0:
                raise HTTPException(409, "Session already has a running turn") from error
            try:
                return await store.acquire_root_run(sessionmaker, session_id, prompt)
            except store.UploadConflictError:
                raise
            except store.TurnConflictError as retry_error:
                raise HTTPException(409, "Session already has a running turn") from retry_error
    except store.UploadConflictError as error:
        raise HTTPException(409, "Session already has an upload in progress") from error
    except IntegrityError:
        # Resolve a deletion that raced the existence check.
        await store.get_session_or_raise(sessionmaker, session_id)
        raise


async def _close_turn(sessionmaker: store.SessionFactory, turn: Turn) -> None:
    try:
        if turn.worker is not None:
            if not turn.finalizing and not turn.worker.done():
                turn.worker.cancel()
            with suppress(asyncio.CancelledError):
                await turn.worker
    finally:
        if not turn.finalized:
            await _finish_root(sessionmaker, turn, "cancelled")


async def _chat_sandbox(
    runtime: Runtime,
    session: store.Session,
    settings: Settings | None = None,
    *,
    owner_run_id: UUID,
) -> AsyncSandbox:
    effective = settings if settings is not None else await _effective_settings(runtime)
    return await _ensure_bound_sandbox(
        runtime,
        effective,
        session.id,
        session.daytona_sandbox_id,
        owner_run_id=owner_run_id,
        needs_cleanup=session.sandbox_needs_cleanup,
    )


def _file_manifest(file: store.File) -> dict:
    return {
        "path": file.path,
        "kind": file.kind,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
    }


async def _hydrate_workspace(
    runtime: Runtime,
    sandbox: AsyncSandbox,
    files: list[store.File],
    timeout: int,
    *,
    reused: bool = False,
) -> int:
    if any(not _valid_file_path(file.path) for file in files):
        raise StorageError("Invalid durable workspace path")
    if reused and files:
        matched = await reconcile_managed_workspace(
            sandbox, {file.path: (file.sha256, file.size_bytes) for file in files}, timeout=timeout
        )
        files = [file for file in files if file.path not in matched]
    else:
        await reset_managed_workspace(sandbox, timeout=timeout)
    with TemporaryDirectory(prefix="fleet-hydrate-") as temporary:
        remaining = iter(files)

        async def restore(worker: int) -> None:
            path = Path(temporary) / str(worker)
            for file in remaining:
                await runtime.storage.download(
                    file.object_key, path, sha256=file.sha256, size=file.size_bytes
                )
                await upload_workspace_file(
                    sandbox,
                    path,
                    file.path,
                    sha256=file.sha256,
                    size=file.size_bytes,
                    timeout=timeout,
                )

        try:
            async with asyncio.TaskGroup() as workers:
                for worker in range(min(4, len(files))):
                    workers.create_task(restore(worker))
        except ExceptionGroup as failures:
            # Preserve the transfer error and its cause after sibling transfers stop.
            error = failures.exceptions[0]
            raise error from error.__cause__
    return len(files)


async def _restore_turn_files(
    runtime: Runtime, sandbox: AsyncSandbox, files: list[store.File], timeout: int, *, reused: bool
) -> None:
    with workspace_span(
        "fleet.workspace.hydrate", attributes={"fleet.file_count": len(files)}
    ) as span:
        restored = await _hydrate_workspace(runtime, sandbox, files, timeout, reused=reused)
        if span is not None:
            record(span.set_attribute, "fleet.restored_file_count", restored)


async def _retire_workspace(runtime: Runtime, sandbox_id: str) -> None:
    try:
        await delete_sandbox_by_id(runtime.daytona, sandbox_id)
    except DaytonaError as error:
        _LOG.warning("Could not retire Daytona VM %s (%s)", sandbox_id, type(error).__name__)


async def _consolidate(
    runtime: Runtime,
    turn: Turn,
    answer: str,
    memories: list[dict],
    trace_id: str | None,
) -> None:
    try:
        async with asyncio.timeout(_MEMORY_ADMISSION_SECONDS):
            await runtime.memory_semaphore.acquire()
    except TimeoutError:
        _LOG.info("Skipped memory consolidation for turn %s (worker at capacity)", turn.run_id)
        return

    async def traced() -> None:
        with (
            capture(runtime.settings),
            workspace_span("fleet.memory.consolidate", root=True) as active,
        ):
            if active is None:
                with without_tracing():
                    await _consolidate_memory(runtime, turn, answer, memories)
                return
            tags = {"fleet.run_id": str(turn.run_id), "fleet.trace.kind": "memory"}
            if trace_id:
                tags["fleet.parent_trace_id"] = trace_id
            record(mlflow.update_current_trace, tags=tags, session_id=str(turn.session_id))
            await _consolidate_memory(runtime, turn, answer, memories)

    try:
        async with asyncio.timeout(_MEMORY_EXECUTION_SECONDS):
            if runtime.tracking_pending or not runtime.settings.mlflow_tracking_uri:
                with without_tracing():
                    await _consolidate_memory(runtime, turn, answer, memories)
            else:
                await asyncio.create_task(traced(), context=Context())
    except TimeoutError:
        _LOG.warning("Memory consolidation timed out for turn %s", turn.run_id)
    finally:
        runtime.memory_semaphore.release()


async def _consolidate_memory(
    runtime: Runtime, turn: Turn, answer: str, memories: list[dict]
) -> None:
    answer = store.transcript_value(answer)

    async def save(candidate: agent.MemoryCandidate) -> None:
        if candidate.content is None:
            return
        await store.save_or_update_memory(
            runtime.sessionmaker,
            session_id=turn.session_id if candidate.scope == "session" else None,
            content=candidate.content,
            approved=candidate.approved,
            memory_id=UUID(candidate.supersedes_id) if candidate.supersedes_id else None,
        )

    try:
        effective = await _effective_settings(runtime)
        active_artifacts = await opt_runner.resolve_active_artifacts(runtime, effective)
        effective = active_artifacts.settings
        decision_lm = active_artifacts.memory_decision_lm or await _request_decision_lm(
            runtime, effective
        )
        candidates = await store.search_memories(
            runtime.sessionmaker,
            turn.session_id,
            f"{turn.prompt}\n{answer}",
            limit=3,
            include_unapproved=True,
        )
        merged = [
            {
                "id": str(record.id),
                "content": record.content,
                "scope": "global" if record.session_id is None else "session",
                "approved": bool(record.approved),
            }
            for record in candidates
        ]
        seen_ids = {item["id"] for item in merged}
        for memory in memories:
            if memory["id"] not in seen_ids:
                merged.append(memory)
                seen_ids.add(memory["id"])
        await agent.consolidate_memory(
            effective,
            turn.prompt,
            answer,
            merged[:3],
            on_save=save,
            decision_lm=decision_lm,
        )
    except Exception as error:
        _LOG.warning("Memory consolidation failed for %s (%s)", turn.run_id, type(error).__name__)
        if runtime.settings.mlflow_tracking_uri and (active := active_span()):
            record(active.set_status, "ERROR")
            record(active.set_attribute, "error.type", type(error).__name__)
            record(
                mlflow.update_current_trace,
                tags={"fleet.outcome": "failed", "fleet.error.stage": "memory"},
            )
    else:
        if runtime.settings.mlflow_tracking_uri and active_span() is not None:
            record(mlflow.update_current_trace, tags={"fleet.outcome": "completed"})


async def _run_turn(
    request: Request,
    runtime: Runtime,
    turn: Turn,
    queue: asyncio.Queue[dict],
    background: BackgroundTasks,
) -> list[dict]:
    if runtime.tracking_pending or not runtime.settings.mlflow_tracking_uri:
        with without_tracing():
            return await _execute_turn(request, runtime, turn, queue, background, trace=None)
    with (
        capture(runtime.settings),
        workspace_span(
            "fleet.turn",
            root=True,
            attributes={"fleet.run_id": str(turn.run_id), "fleet.turn.status": "failed"},
        ) as trace,
    ):
        if trace is None:
            with without_tracing():
                return await _execute_turn(request, runtime, turn, queue, background, trace=None)
        record(
            mlflow.update_current_trace,
            tags={"fleet.run_id": str(turn.run_id), "fleet.trace.kind": "turn"},
            session_id=str(turn.session_id),
            request_preview=preview(turn.prompt),
        )
        try:
            return await _execute_turn(request, runtime, turn, queue, background, trace=trace)
        except Exception:
            record(
                mlflow.update_current_trace,
                tags={"fleet.outcome": "failed", "fleet.error.stage": "turn.finalize"},
                response_preview="failed during turn.finalize",
            )
            raise
        finally:
            _LOG.info("Turn %s MLflow trace %s", turn.run_id, trace.trace_id)


def _trace_link(runtime: Runtime, trace_id: str | None) -> str | None:
    if (
        trace_id is None
        or not runtime.mlflow_experiment_id
        or not runtime.settings.mlflow_tracking_uri
    ):
        return None
    try:
        uri = urlsplit(runtime.settings.mlflow_tracking_uri)
        if uri.scheme not in {"http", "https"} or not uri.hostname or uri.username is not None:
            return None
    except ValueError:
        return None
    experiment = quote(runtime.mlflow_experiment_id, safe="")
    trace_id = quote(trace_id, safe="")
    return urlunsplit(
        (
            uri.scheme,
            uri.netloc,
            uri.path.rstrip("/") + "/",
            "",
            f"/experiments/{experiment}/traces?traceId={trace_id}",
        )
    )


@router.get("/traces/{trace_id}/link")
async def resolve_trace_link(trace_id: str, runtime: RuntimeDep) -> dict[str, str | None]:
    link = _trace_link(runtime, trace_id)
    uri = runtime.settings.mlflow_tracking_uri
    if not link or not uri or not re.fullmatch(r"tr-[a-f0-9]{32}", trace_id):
        return {"url": None}
    try:
        async with asyncio.timeout(3):
            trace = await asyncio.to_thread(
                mlflow.MlflowClient(tracking_uri=uri).get_trace, trace_id, display=False
            )
    except (MlflowException, OSError, TimeoutError):
        return {"url": None}
    experiment = trace.info.trace_location.mlflow_experiment
    if experiment is None or experiment.experiment_id != runtime.mlflow_experiment_id:
        return {"url": None}
    return {"url": link}


async def _persist_turn_outcome(
    sessionmaker: store.SessionFactory,
    turn: Turn,
    status: store.TerminalStatus,
    stream: ui_stream.UiStream,
    *,
    result: dspy.Prediction | None,
    artifacts: list[store.StagedFile],
    error: str | None,
) -> bool:
    completed = status == "completed"
    persisted: dict[str, Any] = {}
    if completed and result is not None:
        persisted = {
            "answer": result.answer,
            "history_json": result.history.model_dump(mode="json"),
            "files": artifacts,
        }
    return await _finish_root(
        sessionmaker,
        turn,
        status,
        child_snapshots=None if completed else stream.pending_child_snapshots(),
        ui_parts=stream.snapshot(str(turn.run_id)),
        error=error,
        **persisted,
    )


async def _persist_failed_finalization(
    sessionmaker: store.SessionFactory,
    turn: Turn,
    status: store.TerminalStatus,
    sandbox: AsyncSandbox | None,
    error: Exception,
    failed_details: dict,
    parts: list[dict],
) -> bool:
    if sandbox is not None:
        turn.sandbox_for_cleanup = sandbox.id
    # A rejected payload must not invalidate the terminal-state retry.
    return await _finish_root(
        sessionmaker,
        turn,
        "cancelled" if status == "cancelled" else "failed",
        error="Could not persist turn",
        ui_parts=[failed_details] if isinstance(error, SQLAlchemyError) else parts,
    )


def _turn_failure_message(
    error: Exception, billing_model: tuple[Literal["primary", "decision"], str] | None
) -> str:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, chatgpt_plan.PlanError):
            return str(cause)
        cause = cause.__cause__
    if type(error.__cause__ or error).__name__ in {"BillingError", "LMBillingError"}:
        if billing_model is None:
            return "Model billing failed. Check the workspace model settings."
        role, label = billing_model
        if role == "decision":
            return (
                f"Billing failed while using the Decision Model ({label}). "
                "Update it in Settings → Models."
            )
        return f"Billing failed while using {label}. Choose another model and retry."
    if isinstance(error, TimeoutError):
        return "Turn timed out"
    if isinstance(error, SandboxStartupError):
        return _WORKSPACE_STARTUP_HELP
    return "Turn failed"


def _active_trace_id() -> str | None:
    active = active_span()
    return active.trace_id if active is not None else None


def _record_skill_route(routing: LiveSpan | None, pinned: list[str], selected: list[str]) -> None:
    if routing is not None:
        record(
            routing.set_attributes,
            {
                "fleet.skills.selected": selected,
                "fleet.skills.route": "pinned" if pinned else "automatic",
                "fleet.skills.outcome": "selected" if selected else "none",
            },
        )


async def _retrieve_turn_memories(
    runtime: Runtime, turn: Turn, selected: list[str]
) -> tuple[list[store.Memory], list[store.Memory], list[store.Memory]]:
    with workspace_span("fleet.memory.retrieve", span_type="RETRIEVER") as retrieval:
        if retrieval is not None:
            record(retrieval.set_inputs, {"selected_skills": selected})
        records = await store.search_memories(runtime.sessionmaker, turn.session_id, turn.prompt)
        procedures = await store.recall_procedures(
            runtime.sessionmaker,
            turn.session_id,
            turn.prompt,
            [name for name, skill in turn.skills.pin_names().items() if skill.name in selected],
        )
        task_memories = records
        if "skill-creator" in selected:
            task_memories = await store.list_approved_session_memories(
                runtime.sessionmaker, turn.session_id
            )
        if retrieval is not None:
            record(
                retrieval.set_outputs,
                {"memories": len(task_memories), "procedures": len(procedures)},
            )
        return records, procedures, task_memories


def _finalization_error(
    status: store.TerminalStatus,
    stage: str,
    error_type: str | None,
    error: Exception,
) -> tuple[str, str | None]:
    return ("turn.finalize", type(error).__name__) if status == "completed" else (stage, error_type)


def _record_turn_outcome(
    trace: LiveSpan | None,
    status: store.TerminalStatus,
    committed: bool,
    timed_out: bool,
    stage: str,
    error_type: str | None,
    telemetry: agent.TurnTelemetry,
    *,
    answer: str | None,
) -> None:
    if trace is None:
        return
    if status == "completed" and not committed:
        status, stage = "failed", "turn.finalize"
    outcome = status
    trace_outcome = "timeout" if timed_out and outcome == "failed" else outcome
    record(
        trace.set_attributes,
        {
            "fleet.turn.status": outcome,
            "fleet.rlm.root_actions": telemetry.root_actions,
            "fleet.rlm.child_rlms": telemetry.child_rlms,
            "fleet.rlm.sub_lm_queries": telemetry.sub_lm_queries,
            "fleet.rlm.completion": telemetry.completion
            if outcome == "completed"
            else trace_outcome,
        },
    )
    record(trace.set_status, "OK" if outcome == "completed" else "ERROR")
    tags = {"fleet.outcome": trace_outcome}
    if outcome == "completed":
        response_preview = preview(answer or "")
    else:
        tags["fleet.error.stage"] = stage
        record(trace.set_attribute, "fleet.error.stage", stage)
        if error_type is not None:
            record(trace.set_attribute, "error.type", error_type)
        response_preview = f"{trace_outcome} during {stage}"
    record(mlflow.update_current_trace, tags=tags, response_preview=response_preview)


async def _execute_turn(
    request: Request,
    runtime: Runtime,
    turn: Turn,
    queue: asyncio.Queue[dict],
    background: BackgroundTasks,
    *,
    trace: LiveSpan | None,
) -> list[dict]:
    sessionmaker = runtime.sessionmaker
    root_id = str(turn.run_id)
    status: store.TerminalStatus = "failed"
    error_text = None
    billing_model: tuple[Literal["primary", "decision"], str] | None = None
    result = None
    terminal_frames: list[dict] = []
    artifacts: list[store.StagedFile] = []
    sandbox: AsyncSandbox | None = None
    hydrated = False
    buffering_terminal = False
    failure_parts = None
    committed = False
    timed_out = False
    stage = "prepare"
    error_type: str | None = None
    telemetry = agent.TurnTelemetry()

    async def send_frame(frame: dict) -> None:
        if buffering_terminal:
            # Publish newly discovered paths and completion after their Neon writes.
            terminal_frames.append(frame)
        else:
            await queue.put(frame)

    async def stage_artifact(key: str, local: Path) -> tuple[str, int]:
        async def register(upload_id: str) -> None:
            await store.register_object_write(
                sessionmaker, turn.session_id, key, upload_id, run_id=turn.run_id
            )

        return await runtime.storage.put(key, local, register=register)

    stream = ui_stream.UiStream(root_id, send_frame)

    def details(outcome: str) -> dict:
        completion = telemetry.completion if outcome == "completed" else outcome
        return {
            "runtime": "dspy.RLM" if telemetry.started_at is not None else None,
            "root_actions": telemetry.root_actions,
            "child_rlms": telemetry.child_rlms,
            "sub_lm_queries": telemetry.sub_lm_queries,
            "elapsed_ms": telemetry.elapsed_ms,
            "completion": completion,
            "trace_url": _trace_link(runtime, trace.trace_id if trace else None),
        }

    async def start_child(child: agent.ChildRun) -> UUID:
        call_id = stream.reserve_call(child.parent_id)
        row = await store.create_child_run(
            sessionmaker, UUID(child.parent_id), kind=child.kind, prompt=child.prompt
        )
        stream.add_child(str(row.id), child.parent_id, call_id)
        return row.id

    async def finish_child(
        child: agent.ChildRun,
        status: agent.ChildStatus,
        answer: str | None,
        error: str | None,
    ) -> None:
        committed = await store.finish_run(
            sessionmaker,
            UUID(child.id),
            status=status,
            answer=answer,
            ui_parts=stream.snapshot(child.id),
            error=error,
        )
        if not committed:
            raise store.TurnConflictError("Child run is no longer active")
        if status == "cancelled":
            stream.discard_child(child.id)

    try:
        async with asyncio.timeout_at(turn.deadline):
            effective = await _effective_settings(runtime)
            active_artifacts = await opt_runner.resolve_active_artifacts(runtime, effective)
            effective = active_artifacts.settings
            optimized_program_state = active_artifacts.rlm_state
            # Read history after acquiring the root so it includes the previous completed turn.
            async with sessionmaker() as db:
                session = await _session(db, turn.session_id)
            prior_turns = await store.get_session_prior_turns(
                sessionmaker,
                turn.session_id,
                max_turns=effective.history_max_turns,
                max_chars=effective.history_max_chars,
            )
            stage = "sandbox.acquire"
            with workspace_span("daytona.sandbox.acquire") as acquisition:
                existing_sandbox_id = session.daytona_sandbox_id
                sandbox = await _chat_sandbox(runtime, session, effective, owner_run_id=turn.run_id)
                if acquisition is not None:
                    record(
                        acquisition.set_attributes,
                        {
                            "daytona.sandbox_id": sandbox.id,
                            "daytona.sandbox.reused": sandbox.id == existing_sandbox_id,
                        },
                    )
            stage = "workspace.hydrate"
            files = await store.list_session_files(sessionmaker, turn.session_id)
            await _restore_turn_files(
                runtime,
                sandbox,
                files,
                effective.daytona_exec_timeout_seconds,
                reused=sandbox.id == existing_sandbox_id,
            )
            hydrated = True
            stage = "skills.route"
            with workspace_span("fleet.skills.route") as routing:
                decision_lm = active_artifacts.skills_decision_lm or await _request_decision_lm(
                    runtime, effective
                )
                billing_model = (
                    "decision",
                    getattr(decision_lm, "model", None) or effective.decision_model,
                )
                selection = await asyncio.to_thread(
                    select_skills,
                    effective,
                    turn.skills,
                    turn.prompt,
                    turn.pinned_skills,
                    prior_turns=prior_turns,
                    decision_lm=decision_lm,
                )
                billing_model = None
                selected_skill_names = [skill.name for skill in selection.active_skills]
                _record_skill_route(routing, turn.pinned_skills, selected_skill_names)
            stage = "memory.retrieve"
            records, procedures, task_memories = await _retrieve_turn_memories(
                runtime, turn, selected_skill_names
            )
            memories = [
                {
                    "id": str(record.id),
                    "content": record.content,
                    "scope": "global" if record.session_id is None else "session",
                    "approved": bool(record.approved),
                }
                for record in records
            ]
            manifest = [_file_manifest(file) for file in files]
            known_files = {file.path: (file.sha256, file.size_bytes) for file in files}

            async def emit(run_id: str, event: agent.RunEvent) -> None:
                # The root result waits for artifact commit. A Python preview forwards a
                # single frame without suspending before the queue put.
                if (run_id == root_id and isinstance(event, agent.RunResultEvent)) or (
                    isinstance(event, agent.PythonExecProgressEvent) and queue.full()
                ):
                    return
                await stream.publish(run_id, event)
                if isinstance(event, agent.RunStartedEvent) and event.kind == "workspace":
                    await stream.skills(
                        run_id,
                        [
                            (skill.name, selection.scores[skill.name])
                            for skill in selection.active_skills
                        ],
                    )
                    await stream.files(run_id, manifest)

            stage = "rlm"
            (root_model, model_label), sub_lm = await _interactive_models(
                request, runtime, effective, session.model_override, session.reasoning_override
            )
            billing_model = ("primary", model_label)
            result = await agent.run_workspace_task(
                effective,
                task=turn.prompt,
                files_manifest=manifest,
                memories=[
                    *(f"Approved procedure\n{record.content}" for record in procedures),
                    *(
                        fact
                        for record in task_memories
                        if (fact := agent.memory_fact(record.content))
                    ),
                ],
                interpreter_factory=make_interpreter_factory(
                    sandbox, effective, asyncio.get_running_loop(), turn_deadline=turn.deadline
                ),
                run_id=root_id,
                emit=emit,
                history=dspy.History.model_validate(session.history_json),
                prior_turns=prior_turns,
                selection=selection,
                host_tools=load_host_tools(runtime.skills, selection, effective),
                model_override=root_model,
                sub_lm=sub_lm,
                optimized_state=optimized_program_state,
                callbacks=agent.ChildCallbacks(start_child, finish_child),
                telemetry=telemetry,
            )
            billing_model = None
            stage = "workspace.publish"
            with (
                workspace_span("fleet.workspace.publish") as publication,
                TemporaryDirectory(prefix="fleet-publish-") as temporary,
            ):
                local = Path(temporary) / "file"
                for artifact in await list_new_artifacts(sandbox, known_files):
                    path = artifact["path"].removeprefix(f"{WORKSPACE}/")
                    if not (_valid_file_path(path) and path.startswith("artifacts/")):
                        continue
                    await download_workspace_file(
                        sandbox, path, local, timeout=effective.daytona_exec_timeout_seconds
                    )
                    key = ObjectStorage.new_key(turn.session_id)
                    digest, size = await stage_artifact(key, local)
                    if (digest, size) != (artifact["sha256"], artifact["size_bytes"]):
                        raise StorageError("Artifact changed during publication")
                    artifacts.append(
                        {
                            "path": path,
                            "kind": "artifact",
                            "object_key": key,
                            "sha256": digest,
                            "size_bytes": size,
                        }
                    )
                if publication is not None:
                    record(publication.set_attribute, "fleet.file_count", len(artifacts))
            if await request.is_disconnected():
                raise asyncio.CancelledError
            failure_parts = stream.snapshot(root_id)
            buffering_terminal = True
            if artifacts:
                updated_files = {entry["path"]: entry for entry in manifest}
                for old_path in store.replaced_file_paths(
                    updated_files, (artifact["path"] for artifact in artifacts)
                ):
                    del updated_files[old_path]
                for entry in artifacts:
                    updated_files[entry["path"]] = {
                        field: value for field, value in entry.items() if field != "object_key"
                    }
                await stream.files(
                    root_id, sorted(updated_files.values(), key=lambda file: file["path"])
                )
            await send_frame(stream.turn_details(details("completed")))
            await stream.publish(root_id, agent.RunResultEvent(result.answer))
            status = "completed"
    except asyncio.CancelledError:
        status = "cancelled"
        error_type = "CancelledError"
        turn.finalizing = True
        if sandbox is not None and not hydrated:
            turn.sandbox_for_cleanup = sandbox.id
        raise
    except Exception as error:
        turn.finalizing = True
        timed_out = isinstance(error, TimeoutError)
        error_type = type(error.__cause__ or error).__name__
        if sandbox is not None and (not hydrated or timed_out or sandbox_cleanup_failed(error)):
            turn.sandbox_for_cleanup = sandbox.id
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            status = "cancelled"
            raise asyncio.CancelledError from error
        error_text = _turn_failure_message(error, billing_model)
        _LOG.warning("Turn %s failed (%s)", turn.run_id, type(error.__cause__ or error).__name__)
    finally:
        turn.finalizing = True
        interruption_frames = []
        if status != "completed":
            interruption_frames = stream.interrupt_web_calls()
            terminal_frames[:] = [
                *interruption_frames,
                stream.turn_details(details("timeout" if timed_out else status)),
            ]
        with workspace_span("fleet.turn.finalize") as finalization:
            fence_committed = False
            try:
                committed = await _persist_turn_outcome(
                    sessionmaker,
                    turn,
                    status,
                    stream,
                    result=result,
                    artifacts=artifacts,
                    error=error_text,
                )
                fence_committed = committed
            except Exception as error:
                stage, error_type = _finalization_error(status, stage, error_type, error)
                _LOG.warning("Could not finalize turn %s (%s)", turn.run_id, type(error).__name__)
                if not turn.finalized:
                    failed_details = stream.turn_details(
                        details("cancelled" if status == "cancelled" else "failed")
                    )
                    # Keep the failed transcript free of an answer that did not commit.
                    if failure_parts is not None:
                        failure_parts.append(failed_details)
                    terminal_frames[:] = [*interruption_frames, failed_details]
                    fence_committed = await _persist_failed_finalization(
                        sessionmaker,
                        turn,
                        status,
                        sandbox,
                        error,
                        failed_details,
                        failure_parts or stream.snapshot(root_id),
                    )
                status = "failed"
                committed = False
            if finalization is not None and not committed:
                record(finalization.set_status, "ERROR")
        with workspace_span("fleet.workspace.cleanup"):
            if fence_committed and turn.sandbox_for_cleanup is not None:
                await _retire_workspace(runtime, turn.sandbox_for_cleanup)
            await _try_object_cleanup(runtime, turn.session_id)
        _record_turn_outcome(
            trace,
            status,
            committed,
            timed_out,
            stage,
            error_type,
            telemetry,
            answer=result.answer if result is not None else None,
        )

    turn.permit.release()
    if status == "completed" and committed and result is not None:
        background.add_task(
            _consolidate,
            runtime,
            turn,
            result.answer,
            memories,
            _active_trace_id(),
        )
        return terminal_frames
    error_frame = ui_stream.error_frame(error_text or "Could not persist turn")
    if status != "completed" and fence_committed:
        return [*terminal_frames, error_frame]
    return [error_frame]


@router.post(
    "/sessions/{session_id}/chat",
    response_class=EventSourceResponse,
    responses={
        409: {"description": "Session already has a running turn"},
        503: {"description": "Turn admission timed out"},
    },
)
async def chat(
    request: Request,
    runtime: RuntimeDep,
    turn: Annotated[Turn, Depends(_prepare_turn)],
    background: BackgroundTasks,
) -> AsyncIterator[ServerSentEvent]:
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    # The worker owns its trace through finalization, independently of the HTTP span.
    turn.worker = asyncio.create_task(
        _run_turn(request, runtime, turn, queue, background), context=Context()
    )
    seq = 0
    while not await request.is_disconnected():
        if turn.worker.done() and queue.empty():
            if not turn.worker.cancelled():
                for frame in turn.worker.result():
                    if await request.is_disconnected():
                        return
                    yield ServerSentEvent(id=f"{turn.run_id}:{seq}", data=frame)
                    seq += 1
                yield ServerSentEvent(raw_data="[DONE]")
            return
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            continue
        if await request.is_disconnected():
            return
        yield ServerSentEvent(id=f"{turn.run_id}:{seq}", data=frame)
        seq += 1
