"""Session-aware FastAPI SSE server for Fleet-RLM Lite."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.utils.callback import BaseCallback
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from mlflow.entities import SpanType
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id
from fleet_rlm.memory_and_eval import (
    AdaptiveFleetEngine,
    VolumePostgresHarness,
    configure_mlflow_observability,
    initialize_database,
)
from fleet_rlm.skills import SkillManager

_LOGGER = logging.getLogger(__name__)
_MAX_TASK_CHARS = 100_000


class TurnRequest(BaseModel):
    """User prompt sent to one Session's Fleet engine."""

    task: str = Field(min_length=1, max_length=_MAX_TASK_CHARS)


@dataclass(slots=True)
class SessionRuntime:
    """All mutable runtime resources for one private Session."""

    sandbox_mgr: DaytonaSandboxManager
    harness: VolumePostgresHarness
    skill_mgr: SkillManager
    engine: AdaptiveFleetEngine
    turn_lock: asyncio.Lock


class SessionRuntimeRegistry:
    """Lazily build a separate sandbox, namespace, and engine per Session."""

    def __init__(self, settings: FleetSettings, lms: tuple[Any, Any, Any, Any | None]):
        self.settings = settings
        self.root_lm, self.sub_lm, self.reflection_lm, self.jev_lm = lms
        self._sessions: dict[str, SessionRuntime] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> SessionRuntime:
        session_id = validate_session_id(session_id)
        async with self._lock:
            runtime = self._sessions.get(session_id)
            if runtime is not None:
                return runtime
            if len(self._sessions) >= self.settings.max_active_sessions:
                raise RuntimeError("The maximum number of active Sessions has been reached")
            sandbox_mgr = DaytonaSandboxManager(
                api_key=self.settings.fleet_daytona_api_key,
                org_id=self.settings.fleet_daytona_org_id,
                volume_id=self.settings.fleet_daytona_volume_id,
                session_id=session_id,
                snapshot=self.settings.fleet_daytona_snapshot,
                auto_stop_minutes=self.settings.daytona_auto_stop_minutes,
                sub_lm=self.sub_lm,
                jev_lm=self.jev_lm,
                max_llm_calls=self.settings.max_llm_calls_per_turn,
            )
            harness = VolumePostgresHarness(self.settings.fleet_database_url, sandbox_mgr)
            compiled_path = Path(self.settings.compiled_program_dir) / f"{session_id}.json"
            runtime = SessionRuntime(
                sandbox_mgr=sandbox_mgr,
                harness=harness,
                skill_mgr=SkillManager(sandbox_mgr, Path(self.settings.local_skills_dir)),
                engine=AdaptiveFleetEngine(
                    sandbox_mgr=sandbox_mgr,
                    root_lm=self.root_lm,
                    sub_lm=self.sub_lm,
                    jev_lm=self.jev_lm,
                    compiled_path=str(compiled_path),
                ),
                turn_lock=asyncio.Lock(),
            )
            await asyncio.to_thread(harness.ensure_session, session_id)
            self._sessions[session_id] = runtime
            return runtime

    async def close(self) -> None:
        async with self._lock:
            runtimes = tuple(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(asyncio.to_thread(runtime.sandbox_mgr.shutdown) for runtime in runtimes))


class SSERuntimeCallback(BaseCallback):
    """Send bounded activity metadata to the active SSE stream."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict[str, Any]]):
        self.loop = loop
        self.queue = queue

    def _emit(self, payload: dict[str, Any]) -> None:
        def enqueue() -> None:
            if self.queue.full():
                with suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
            self.queue.put_nowait(payload)

        self.loop.call_soon_threadsafe(enqueue)

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        self._emit({"event": "module_start", "module": instance.__class__.__name__})

    def on_tool_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        self._emit({"event": "tool_start", "tool": str(getattr(instance, "name", instance.__class__.__name__))[:100]})

    def on_tool_end(
        self,
        call_id: str,  # noqa: ARG002
        outputs: dict[str, Any] | None,  # noqa: ARG002
        exception: BaseException | None = None,
    ) -> None:
        self._emit(
            {
                "event": "tool_end",
                "ok": exception is None,
                "error_type": type(exception).__name__ if exception else None,
            }
        )


def create_app(
    *,
    settings: FleetSettings | None = None,
    initialize_db: bool = True,
    configure_observability: bool = True,
) -> FastAPI:
    """Build the Lite API. External resources are acquired by application lifespan."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if initialize_db:
            await asyncio.to_thread(initialize_database, resolved.fleet_database_url)
        if configure_observability:
            try:
                application.state.mlflow_experiment_id = await asyncio.to_thread(
                    configure_mlflow_observability,
                    resolved.mlflow_experiment_name,
                    resolved.mlflow_tracking_uri,
                    trace_catalog=resolved.mlflow_trace_catalog,
                    trace_schema=resolved.mlflow_trace_schema,
                    trace_table_prefix=resolved.mlflow_trace_table_prefix,
                    tracing_sql_warehouse_id=resolved.mlflow_tracing_sql_warehouse_id,
                )
            except Exception as exc:
                _LOGGER.warning("MLflow tracing could not be configured (%s)", type(exc).__name__)
                application.state.mlflow_experiment_id = None
        else:
            application.state.mlflow_experiment_id = None
        lms = resolved.build_lms()
        application.state.settings = resolved
        application.state.lms = lms
        application.state.session_registry = SessionRuntimeRegistry(resolved, lms)
        try:
            yield
        finally:
            await application.state.session_registry.close()

    app = FastAPI(title="Fleet-RLM Lite API", version="0.8.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.lms = None
    app.state.session_registry = None
    app.state.mlflow_experiment_id = None

    def registry_for(request: Request) -> SessionRuntimeRegistry:
        registry = request.app.state.session_registry
        if registry is None:
            raise HTTPException(status_code=503, detail="Fleet runtime is not ready")
        return registry

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.8.0"}

    @app.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
        try:
            with psycopg.Connection[dict[str, Any]].connect(
                resolved.fleet_database_url, row_factory=dict_row
            ) as connection:
                rows = connection.execute(
                    "SELECT session_id, updated_at, jsonb_array_length(turns) AS turn_count "
                    "FROM fleet_lite_sessions ORDER BY updated_at DESC LIMIT 100"
                ).fetchall()
            return {"sessions": [dict(row) for row in rows]}
        except Exception as exc:
            _LOGGER.warning("Session listing failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Session storage is unavailable") from None

    @app.get("/skills")
    async def get_skills_catalog(request: Request, session_id: str = "default") -> dict[str, str]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.skill_mgr.sync_local_skills_to_volume)
                catalog = await asyncio.to_thread(runtime.skill_mgr.build_catalog_xml)
            return {"catalog_xml": catalog}
        except ValueError as exc:
            _LOGGER.warning("Local Skill catalog could not be read (%s)", type(exc).__name__)
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:
            _LOGGER.warning("Skill catalog could not be read (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Session Skill catalog is unavailable") from None

    @app.post("/skills/pull")
    async def pull_skills_to_local(request: Request, session_id: str = "default") -> dict[str, list[str]]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                exported = await asyncio.to_thread(runtime.skill_mgr.export_volume_skills_to_local)
            return {"exported": exported}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:
            _LOGGER.warning("Skill export failed for Session %s (%s)", session_id, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Skill export is unavailable") from None

    @app.get("/sessions/{session_id}/files")
    async def list_files(session_id: str, request: Request) -> dict[str, Any]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                files = await asyncio.to_thread(runtime.sandbox_mgr.list_workspace_files)
            return {
                "session_id": session_id,
                "files": files,
                "artifacts": [path for path in files if path.startswith("artifacts/")],
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:
            _LOGGER.warning("Workspace listing failed for Session %s (%s)", session_id, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Session workspace is unavailable") from None

    @app.post("/sessions/{session_id}/attach")
    async def attach_file(
        session_id: str,
        request: Request,
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict[str, str]:
        try:
            runtime = await registry_for(request).get(session_id)
            raw_name = (file.filename or "attachment").replace("\\", "/").split("/")[-1]
            if not raw_name or raw_name in {".", ".."} or "\x00" in raw_name:
                raise HTTPException(status_code=422, detail="Invalid attachment filename")
            chunks: list[bytes] = []
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > resolved.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Attachment exceeds the configured size limit")
                chunks.append(chunk)
            relative_path = f"files/{raw_name}"
            content = b"".join(chunks)
            async with runtime.turn_lock:
                mounted_path = await asyncio.to_thread(runtime.sandbox_mgr.write_workspace_file, relative_path, content)
                await asyncio.to_thread(runtime.harness.persist_workspace_files, session_id)
            return {"mounted_path": mounted_path, "rel_path": relative_path}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:
            _LOGGER.warning("Attachment failed for Session %s (%s)", session_id, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Attachment storage is unavailable") from None
        finally:
            await file.close()

    @app.post(
        "/sessions/{session_id}/turn",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Server-Sent Events for Turn status, activity, completion, or failure.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def stream_turn(session_id: str, req: TurnRequest, request: Request) -> StreamingResponse:
        try:
            runtime = await registry_for(request).get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:
            _LOGGER.warning("Could not initialize Session %s (%s)", session_id, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Session runtime is unavailable") from None

        async def event_generator():
            async with runtime.turn_lock:
                loop = asyncio.get_running_loop()
                mlflow_experiment_id = request.app.state.mlflow_experiment_id
                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
                callback = SSERuntimeCallback(loop, queue)
                request_id = uuid.uuid4().hex

                def enqueue(payload: dict[str, Any]) -> None:
                    if queue.full():
                        with suppress(asyncio.QueueEmpty):
                            queue.get_nowait()
                    queue.put_nowait(payload)

                def run_turn() -> None:
                    try:
                        if mlflow_experiment_id:
                            mlflow.set_experiment(experiment_id=mlflow_experiment_id)
                        memory_summary, session_history = runtime.harness.hydrate_volume_from_postgres(session_id)
                        runtime.skill_mgr.sync_local_skills_to_volume()
                        files = runtime.sandbox_mgr.list_workspace_files()
                        compiled_artifact = "memory/compiled_flex_fleet.json"
                        if compiled_artifact in files:
                            program = runtime.sandbox_mgr.read_workspace_file(compiled_artifact, max_bytes=10_000_000)
                            runtime.engine.load_compiled_program(program)
                        manifest = ", ".join(f"/workspace/{path}" for path in files) or "/workspace/files (empty)"
                        skills_catalog = runtime.skill_mgr.build_catalog_xml()
                        with mlflow.start_span(name="fleet_turn", span_type=SpanType.CHAIN) as span:
                            span.set_inputs({"session_id": session_id, "task": req.task})
                            mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})
                            callbacks = [*dspy.settings.callbacks, callback]
                            with dspy.context(callbacks=callbacks, disable_history=True):
                                prediction = runtime.engine(
                                    workspace_manifest=manifest,
                                    skills_catalog=skills_catalog,
                                    memory_summary=memory_summary,
                                    session_history=session_history,
                                    task=req.task,
                                )
                            answer = str(prediction.answer)
                            learned_note = str(getattr(prediction, "learned_skill_note", ""))[:10_000]
                            route = str(getattr(prediction, "route", "single_rlm"))
                            turn_record = {
                                "task": req.task,
                                "workspace_manifest": manifest,
                                "skills_catalog": skills_catalog,
                                "answer": answer,
                                "route": route,
                                "learned_skill_note": learned_note,
                            }
                            artifacts = runtime.harness.sync_volume_to_postgres(
                                session_id=session_id,
                                turn_record=turn_record,
                                new_memory_note=learned_note,
                            )
                            span.set_outputs({"answer": answer, "route": route, "artifacts": artifacts})
                        trace_id = mlflow.get_last_active_trace_id() if mlflow_experiment_id else None
                        completion_event = {
                            "event": "turn_complete",
                            "answer": answer,
                            "route": route,
                            "learned_skill_note": learned_note,
                            "artifacts": artifacts,
                        }
                        if trace_id:
                            completion_event["trace_id"] = trace_id
                        loop.call_soon_threadsafe(
                            enqueue,
                            completion_event,
                        )
                    except Exception as exc:
                        _LOGGER.error("Turn %s failed for Session %s (%s)", request_id, session_id, type(exc).__name__)
                        loop.call_soon_threadsafe(
                            enqueue,
                            {
                                "event": "error",
                                "request_id": request_id,
                                "error": f"The Turn failed (request {request_id}).",
                            },
                        )
                    finally:
                        loop.call_soon_threadsafe(enqueue, {"event": "done"})

                future = loop.run_in_executor(None, run_turn)
                try:
                    yield "data: " + json.dumps({"event": "turn_status", "phase": "preparation"}) + "\n\n"
                    connected = True
                    while True:
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=15)
                        except TimeoutError:
                            if connected and await request.is_disconnected():
                                connected = False
                            if connected:
                                yield ": keep-alive\n\n"
                            continue
                        if event.get("event") == "done":
                            break
                        if connected and await request.is_disconnected():
                            connected = False
                        if connected:
                            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    await future
                finally:
                    # Keep this Session serialized until sandbox work has drained after disconnect.
                    if not future.done():
                        with suppress(asyncio.CancelledError):
                            await asyncio.shield(future)

        return StreamingResponse(
            event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app


app = create_app()
