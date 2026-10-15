"""Session-aware FastAPI SSE server for Fleet-RLM Lite."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.utils.callback import BaseCallback
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from mlflow.entities import SpanType
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from fleet_rlm.config import FleetSettings, get_settings
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id
from fleet_rlm.memory_and_eval import (
    AdaptiveFleetEngine,
    VolumePostgresHarness,
    configure_mlflow_observability,
    initialize_database,
)
from fleet_rlm.skills import SkillManager
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_TASK_CHARS = 100_000


class TurnRequest(BaseModel):
    """User prompt sent to one Session's Fleet engine."""

    task: str = Field(min_length=1, max_length=_MAX_TASK_CHARS)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(rlm|flex|hybrid)$")


class WorkspaceWriteRequest(BaseModel):
    content: str | None = Field(default=None, max_length=25_000_000)
    content_base64: str | None = Field(default=None, max_length=34_000_000)


class ContextRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=2000)


class MemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    kind: str = Field(default="observation", pattern="^(observation|preference|hypothesis|procedure)$")
    sources: dict[str, str] = Field(default_factory=dict, max_length=100)
    reviewed: bool = False


class ModelRequest(BaseModel):
    role: str = Field(default="root", pattern="^(root|sub)$")
    model: str = Field(
        min_length=1, max_length=300, pattern=r"^openai/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+$"
    )


@dataclass(slots=True)
class SessionRuntime:
    """All mutable runtime resources for one private Session."""

    sandbox_mgr: DaytonaSandboxManager
    harness: VolumePostgresHarness
    skill_mgr: SkillManager
    engine: AdaptiveFleetEngine
    turn_lock: asyncio.Lock
    execution_mode: str = "hybrid"
    context: Any | None = None
    lease: psycopg.Connection | None = None


class SessionRuntimeRegistry:
    """Lazily build a separate sandbox, namespace, and engine per Session."""

    def __init__(self, settings: FleetSettings, lms: tuple[Any, Any, Any, Any | None] | None):
        self.settings = settings
        self.root_lm, self.sub_lm, self.reflection_lm, self.jev_lm = lms or (None, None, None, None)
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
            if self.root_lm is None:
                self.root_lm, self.sub_lm, self.reflection_lm, self.jev_lm = await asyncio.to_thread(
                    self.settings.build_lms
                )
            assert self.sub_lm is not None
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
                max_depth=self.settings.max_agent_depth,
                max_children=self.settings.max_child_sandboxes,
                max_active_children=self.settings.max_active_children,
                turn_timeout_seconds=self.settings.turn_timeout_seconds,
                linked_children=self.settings.linked_children,
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
                    execution_mode=getattr(self.settings, "execution_mode", "hybrid"),
                ),
                turn_lock=asyncio.Lock(),
                execution_mode=getattr(self.settings, "execution_mode", "hybrid"),
            )
            await asyncio.to_thread(harness.ensure_session, session_id)
            runtime.lease = await asyncio.to_thread(harness.acquire_session_lease)
            try:
                saved = await asyncio.to_thread(harness.runtime_settings)
                runtime.execution_mode = saved.get("mode", runtime.execution_mode)
                runtime.engine.execution_mode = runtime.execution_mode
                if saved.get("models"):
                    runtime.engine = await asyncio.to_thread(self.engine_for_models, runtime, saved["models"])
                    runtime.sandbox_mgr.sub_lm = runtime.engine.sub_lm
                if saved.get("context"):
                    runtime.context = WorkspaceDataset(**saved["context"])
            except BaseException:
                runtime.lease.close()
                raise
            self._sessions[session_id] = runtime
            return runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> AdaptiveFleetEngine:
        settings = self.settings.model_copy(
            update={"fleet_root_model": models["root"], "fleet_sub_model": models["sub"]}
        )
        root, sub, _, _ = settings.build_lms()
        return AdaptiveFleetEngine(
            sandbox_mgr=runtime.sandbox_mgr,
            root_lm=root,
            sub_lm=sub,
            jev_lm=self.jev_lm,
            compiled_path=runtime.engine.compiled_path,
            execution_mode=runtime.execution_mode,
        )

    async def close(self) -> None:
        async with self._lock:
            runtimes = tuple(self._sessions.values())
            self._sessions.clear()
        try:
            await asyncio.gather(*(asyncio.to_thread(runtime.sandbox_mgr.shutdown) for runtime in runtimes))
        finally:
            for runtime in runtimes:
                if runtime.lease is not None:
                    await asyncio.to_thread(runtime.lease.close)


class SSERuntimeCallback(BaseCallback):
    """Send bounded activity metadata to the active SSE stream."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[dict[str, Any]],
        manager: DaytonaSandboxManager | None = None,
    ):
        self.loop = loop
        self.queue = queue
        self.manager = manager
        self._closed = threading.Event()
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()

    def close(self) -> None:
        self._closed.set()
        with self._pending_lock:
            for future in self._pending:
                future.cancel()

    def _emit(self, payload: dict[str, Any]) -> None:
        # Backpressure reaches the producer rather than discarding a prior outcome.
        if self._closed.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(self.queue.put(payload), self.loop)
        with self._pending_lock:
            self._pending.add(future)
            if self._closed.is_set():
                future.cancel()
        try:
            future.result(timeout=30)
        except FutureCancelledError:
            if not self._closed.is_set():
                raise
        except TimeoutError:
            future.cancel()
            raise TimeoutError("SSE consumer is too slow") from None
        finally:
            with self._pending_lock:
                self._pending.discard(future)

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
        application.state.settings = resolved
        application.state.lms = None
        application.state.session_registry = SessionRuntimeRegistry(resolved, None)
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
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
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

    @app.get("/models")
    async def list_models(scope: str = "system.ai") -> dict[str, Any]:
        try:
            return {"scope": scope, "models": await asyncio.to_thread(resolved.list_models, scope)}
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Invalid discovery scope or missing Gateway credentials"
            ) from None
        except Exception:
            raise HTTPException(status_code=503, detail="Gateway model discovery is unavailable") from None

    @app.get("/sessions/{session_id}/config")
    async def session_config(session_id: str, request: Request) -> dict[str, Any]:
        try:
            runtime = await registry_for(request).get(session_id)
            return {
                "mode": runtime.execution_mode,
                "models": {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model},
                "context": asdict(runtime.context) if runtime.context else None,
                "limits": {
                    "lm_calls": runtime.sandbox_mgr.max_llm_calls,
                    "depth": runtime.sandbox_mgr.max_depth,
                    "children": runtime.sandbox_mgr.max_children,
                    "active_children": runtime.sandbox_mgr.max_active_children,
                    "timeout_seconds": runtime.sandbox_mgr.turn_timeout_seconds,
                },
            }
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid Session ID") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Session configuration is unavailable") from None

    @app.put("/sessions/{session_id}/model")
    async def select_model(session_id: str, body: ModelRequest, request: Request) -> dict[str, str]:
        try:
            scope = ".".join(body.model.removeprefix("openai/").split(".")[:2])
            available = await asyncio.to_thread(resolved.list_models, scope)
            if body.model not in available:
                raise HTTPException(status_code=422, detail="Model is not an accessible chat service")
            registry = registry_for(request)
            runtime = await registry.get(session_id)
            if runtime.turn_lock.locked():
                raise HTTPException(status_code=409, detail="Wait for the active turn before changing models")
            async with runtime.turn_lock:
                models = {"root": runtime.engine.root_lm.model, "sub": runtime.engine.sub_lm.model}
                models[body.role] = body.model
                engine = await asyncio.to_thread(registry.engine_for_models, runtime, models)
                await asyncio.to_thread(runtime.harness.save_runtime_settings, {"models": models})
                runtime.engine = engine
                runtime.sandbox_mgr.sub_lm = engine.sub_lm
            return {"role": body.role, "model": body.model}
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid session or model configuration") from None
        except Exception:
            raise HTTPException(
                status_code=503, detail="Model selection is unavailable; configuration unchanged"
            ) from None

    @app.get("/sessions/{session_id}/mode")
    async def get_mode(session_id: str, request: Request) -> dict[str, str]:
        try:
            runtime = await registry_for(request).get(session_id)
            return {"mode": runtime.execution_mode}
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid Session ID") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Session runtime is unavailable") from None

    @app.put("/sessions/{session_id}/mode")
    async def set_mode(session_id: str, body: ModeRequest, request: Request) -> dict[str, str]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                runtime.execution_mode = body.mode
                if hasattr(runtime.engine, "execution_mode"):
                    runtime.engine.execution_mode = body.mode
                await asyncio.to_thread(
                    runtime.harness.save_runtime_settings,
                    {
                        "mode": body.mode,
                        "context": asdict(runtime.context) if runtime.context else None,
                    },
                )
            return {"mode": body.mode}
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid Session ID or mode") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Session runtime is unavailable") from None

    @app.get("/sessions/{session_id}/history")
    async def get_history(session_id: str, request: Request) -> dict[str, Any]:
        try:
            await registry_for(request).get(session_id)
            with psycopg.Connection[dict[str, Any]].connect(
                resolved.fleet_database_url, row_factory=dict_row
            ) as connection:
                row = connection.execute(
                    "SELECT turns FROM fleet_lite_sessions WHERE session_id = %s", (session_id,)
                ).fetchone()
            return {"session_id": session_id, "turns": (row or {}).get("turns", [])}
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid Session ID") from None
        except Exception:
            _LOGGER.warning("History read failed for Session %s", session_id)
            raise HTTPException(status_code=503, detail="Session history is unavailable") from None

    @app.get("/sessions/{session_id}/workspace/tree")
    async def workspace_tree(session_id: str, request: Request) -> dict[str, Any]:
        return await list_files(session_id, request)

    @app.get("/sessions/{session_id}/turns/{turn_id}")
    async def turn_status(session_id: str, turn_id: str, request: Request) -> dict[str, Any]:
        try:
            runtime = await registry_for(request).get(session_id)
            result = await asyncio.to_thread(runtime.harness.turn_status, turn_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Turn was not found")
            return result
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid session or turn ID") from None

    @app.get("/sessions/{session_id}/memory")
    async def list_memory(session_id: str, request: Request, q: str = "") -> dict[str, Any]:
        runtime = await registry_for(request).get(session_id)
        return {"memories": await asyncio.to_thread(runtime.harness.memories, q)}

    @app.put("/sessions/{session_id}/memory/{memory_id}")
    async def put_memory(session_id: str, memory_id: str, body: MemoryRequest, request: Request) -> dict[str, str]:
        runtime = await registry_for(request).get(session_id)
        try:
            async with runtime.turn_lock:
                await asyncio.to_thread(
                    runtime.harness.save_memory,
                    memory_id,
                    body.content,
                    sources=body.sources,
                    kind=body.kind,
                    reviewed=body.reviewed,
                )
            return {"memory_id": memory_id}
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid memory") from None

    @app.delete("/sessions/{session_id}/memory/{memory_id}")
    async def remove_memory(session_id: str, memory_id: str, request: Request) -> dict[str, str]:
        runtime = await registry_for(request).get(session_id)
        async with runtime.turn_lock:
            await asyncio.to_thread(runtime.harness.delete_memory, memory_id)
        return {"deleted": memory_id}

    @app.get("/sessions/{session_id}/workspace/stat/{rel_path:path}")
    async def workspace_stat(session_id: str, rel_path: str, request: Request) -> dict[str, Any]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                content = await asyncio.to_thread(runtime.sandbox_mgr.read_workspace_file, rel_path)
            return {
                "path": rel_path,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid workspace path") from None
        except Exception:
            raise HTTPException(status_code=404, detail="Workspace file is unavailable") from None

    @app.get("/sessions/{session_id}/workspace/files/{rel_path:path}")
    async def read_workspace_file(session_id: str, rel_path: str, request: Request) -> Response:
        temporary_path: str | None = None
        try:
            runtime = await registry_for(request).get(session_id)
            descriptor, temporary_path = tempfile.mkstemp(prefix="fleet-download-")
            os.close(descriptor)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                await asyncio.to_thread(
                    runtime.sandbox_mgr.read_workspace_file_to_path,
                    rel_path,
                    temporary_path,
                    max_bytes=25_000_000,
                )
            media_type = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
            response = FileResponse(
                temporary_path,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{Path(rel_path).name}"'},
                background=BackgroundTask(os.unlink, temporary_path),
            )
            temporary_path = None
            return response
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid workspace path") from None
        except Exception:
            raise HTTPException(status_code=404, detail="Workspace file is unavailable") from None
        finally:
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_path)

    @app.put("/sessions/{session_id}/workspace/files/{rel_path:path}")
    async def write_workspace_file(
        session_id: str, rel_path: str, body: WorkspaceWriteRequest, request: Request
    ) -> dict[str, Any]:
        try:
            if (body.content is None) == (body.content_base64 is None):
                raise HTTPException(status_code=422, detail="Supply exactly one of content or content_base64")
            content = (
                body.content.encode("utf-8")
                if body.content is not None
                else base64.b64decode(body.content_base64 or "", validate=True)
            )
            if len(content) > 25_000_000:
                raise HTTPException(status_code=413, detail="Workspace file exceeds the 25 MB limit")
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                mounted_path = await asyncio.to_thread(runtime.sandbox_mgr.write_workspace_file, rel_path, content)
                await asyncio.to_thread(runtime.harness.persist_workspace_files, session_id)
            return {
                "path": rel_path,
                "mounted_path": mounted_path,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid workspace path or content") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Workspace write failed") from None

    @app.delete("/sessions/{session_id}/workspace/files/{rel_path:path}")
    async def delete_workspace_file(session_id: str, rel_path: str, request: Request) -> dict[str, str]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                await asyncio.to_thread(runtime.sandbox_mgr.delete_workspace_file, rel_path)
                await asyncio.to_thread(runtime.harness.persist_workspace_files, session_id)
            return {"deleted": rel_path}
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid workspace path") from None
        except Exception:
            raise HTTPException(status_code=404, detail="Workspace file could not be deleted") from None

    @app.put("/sessions/{session_id}/context")
    async def select_context(session_id: str, body: ContextRequest, request: Request) -> dict[str, Any]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                content = await asyncio.to_thread(runtime.sandbox_mgr.read_workspace_file, body.path)
                runtime.context = WorkspaceDataset(
                    workspace_path=body.path,
                    description=body.description,
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
                await asyncio.to_thread(
                    runtime.harness.save_runtime_settings,
                    {
                        "mode": runtime.execution_mode,
                        "context": asdict(runtime.context),
                    },
                )
            return {"path": body.path, "size_bytes": len(content), "sha256": runtime.context.sha256}
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid context selection") from None
        except Exception:
            raise HTTPException(status_code=503, detail="Context selection is unavailable") from None

    @app.delete("/sessions/{session_id}/context")
    async def clear_context(session_id: str, request: Request) -> dict[str, str]:
        runtime = await registry_for(request).get(session_id)
        async with runtime.turn_lock:
            runtime.context = None
            await asyncio.to_thread(
                runtime.harness.save_runtime_settings,
                {
                    "mode": runtime.execution_mode,
                    "context": None,
                },
            )
        return {"status": "cleared"}

    @app.post("/skills/push")
    async def push_skills(request: Request, session_id: str = "default") -> dict[str, int]:
        try:
            runtime = await registry_for(request).get(session_id)
            async with runtime.turn_lock:
                count = await asyncio.to_thread(runtime.skill_mgr.sync_local_skills_to_volume)
            return {"pushed": int(count)}
        except Exception:
            raise HTTPException(status_code=503, detail="Skill push is unavailable") from None

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
            descriptor, temporary_path = tempfile.mkstemp(prefix="fleet-upload-")
            size = 0
            try:
                with os.fdopen(descriptor, "wb") as output:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > min(resolved.max_upload_bytes, 25_000_000):
                            raise HTTPException(
                                status_code=413,
                                detail="Attachment exceeds the configured size limit",
                            )
                        output.write(chunk)
                relative_path = f"files/{raw_name}"
                async with runtime.turn_lock:
                    await asyncio.to_thread(runtime.harness.hydrate_volume_from_postgres, session_id)
                    mounted_path = await asyncio.to_thread(
                        runtime.sandbox_mgr.write_workspace_file_from_path,
                        relative_path,
                        temporary_path,
                    )
                    await asyncio.to_thread(runtime.harness.persist_workspace_files, session_id)
                return {"mounted_path": mounted_path, "rel_path": relative_path}
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_path)
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
        response_class=EventSourceResponse,
        responses={
            200: {
                "description": "Server-Sent Events for Turn status, activity, completion, or failure.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def stream_turn(session_id: str, req: TurnRequest, request: Request):
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
                callback = SSERuntimeCallback(loop, queue, runtime.sandbox_mgr)
                request_id = uuid.uuid4().hex

                def enqueue(payload: dict[str, Any]) -> None:
                    callback._emit(payload)

                def run_turn() -> None:
                    callback_manager = runtime.sandbox_mgr
                    previous_event_callback = getattr(callback_manager, "event_callback", None)
                    try:
                        if hasattr(callback_manager, "event_callback"):
                            callback_manager.event_callback = enqueue
                        if hasattr(callback_manager, "reset_turn_budget"):
                            callback_manager.reset_turn_budget()
                        if hasattr(runtime.harness, "claim_turn"):
                            prior = runtime.harness.claim_turn(request_id, req.idempotency_key or request_id, req.task)
                            if prior is not None:
                                enqueue(
                                    prior["result"]
                                    if prior["status"] == "completed"
                                    else {
                                        "event": "error",
                                        "request_id": prior["turn_id"],
                                        "error": "Prior turn has an uncertain or failed outcome; it was not replayed.",
                                    }
                                )
                                return
                        if mlflow_experiment_id:
                            mlflow.set_experiment(experiment_id=mlflow_experiment_id)
                        memory_summary, session_history = runtime.harness.hydrate_volume_from_postgres(session_id)
                        runtime.skill_mgr.sync_local_skills_to_volume()
                        files = runtime.sandbox_mgr.list_workspace_files()
                        # Workspace files are model-writable, so they cannot approve executable programs.
                        manifest = ", ".join(f"/workspace/{path}" for path in files) or "/workspace/files (empty)"
                        skills_catalog = runtime.skill_mgr.build_catalog_xml()
                        if hasattr(runtime.harness, "memories"):
                            memories = runtime.harness.memories(req.task, reviewed_only=True)
                            memory_summary += "\n" + str(memories)[:12000]
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
                                    execution_mode=getattr(runtime, "execution_mode", "hybrid"),
                                    context=getattr(runtime, "context", None),
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
                                "turn_id": request_id,
                                "idempotency_key": req.idempotency_key,
                                "execution_mode": getattr(runtime, "execution_mode", "hybrid"),
                                "trace_id": getattr(span, "trace_id", None) if mlflow_experiment_id else None,
                                "context_files": {
                                    runtime.context.workspace_path: runtime.context.sha256,
                                }
                                if isinstance(getattr(runtime, "context", None), WorkspaceDataset)
                                else {},
                            }
                            artifacts = runtime.harness.sync_volume_to_postgres(
                                session_id=session_id,
                                turn_record=turn_record,
                                new_memory_note=learned_note,
                            )
                            span.set_outputs({"answer": answer, "route": route, "artifacts": artifacts})
                        trace_id = turn_record.get("trace_id") or (
                            mlflow.get_last_active_trace_id() if mlflow_experiment_id else None
                        )
                        completion_event = {
                            "event": "turn_complete",
                            "answer": answer,
                            "route": route,
                            "learned_skill_note": learned_note,
                            "artifacts": artifacts,
                        }
                        if trace_id:
                            completion_event["trace_id"] = trace_id
                        enqueue(completion_event)
                    except Exception as exc:
                        _LOGGER.error("Turn %s failed for Session %s (%s)", request_id, session_id, type(exc).__name__)
                        cancelled = getattr(callback_manager, "_cancelled", None)
                        was_cancelled = cancelled is not None and cancelled.is_set()
                        error_event = {
                            "event": "turn_cancelled" if was_cancelled else "error",
                            "request_id": request_id,
                            "error": f"The Turn failed (request {request_id}).",
                        }
                        if hasattr(runtime.harness, "finish_turn"):
                            with suppress(Exception):
                                runtime.harness.finish_turn(
                                    request_id, "cancelled" if was_cancelled else "failed", error_event
                                )
                        enqueue(error_event)
                    finally:
                        if hasattr(callback_manager, "end_turn"):
                            callback_manager.end_turn()
                        if hasattr(callback_manager, "event_callback"):
                            callback_manager.event_callback = previous_event_callback
                        enqueue({"event": "done"})

                future = loop.run_in_executor(None, run_turn)
                sequence = 0

                def wire_event(event: dict[str, Any]) -> dict[str, Any]:
                    nonlocal sequence
                    sequence += 1
                    payload = {key: value for key, value in event.items() if key != "event"}
                    return {
                        "version": 1,
                        "event": event.get("event"),
                        "session_id": session_id,
                        "turn_id": request_id,
                        "agent_id": None,
                        "parent_agent_id": None,
                        "sandbox_id": getattr(getattr(runtime.sandbox_mgr, "_sandbox", None), "id", None),
                        "seq": sequence,
                        "timestamp": time.time(),
                        "payload": payload,
                        **payload,
                    }

                try:
                    status_event = wire_event({"event": "turn_status", "phase": "preparation"})
                    yield ServerSentEvent(data=status_event, id=str(status_event["seq"]))
                    connected = True
                    while True:
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=15)
                        except TimeoutError:
                            if connected and await request.is_disconnected():
                                connected = False
                                cancel = getattr(runtime.sandbox_mgr, "cancel_turn", None)
                                if cancel:
                                    with suppress(Exception):
                                        await asyncio.to_thread(cancel)
                            continue
                        if event.get("event") == "done":
                            break
                        if connected and await request.is_disconnected():
                            connected = False
                            cancel = getattr(runtime.sandbox_mgr, "cancel_turn", None)
                            if cancel:
                                with suppress(Exception):
                                    await asyncio.to_thread(cancel)
                        if connected:
                            envelope = wire_event(event)
                            yield ServerSentEvent(data=envelope, id=str(envelope["seq"]))
                    await future
                finally:
                    # Keep this Session serialized until sandbox work has drained after disconnect.
                    callback.close()
                    if not future.done():
                        cancel = getattr(runtime.sandbox_mgr, "cancel_turn", None)
                        if cancel:
                            with suppress(Exception):
                                await asyncio.to_thread(cancel)
                        while not future.done():
                            try:
                                await asyncio.shield(future)
                            except asyncio.CancelledError:
                                continue

        async for event in event_generator():
            yield event

    return app


app = create_app()
