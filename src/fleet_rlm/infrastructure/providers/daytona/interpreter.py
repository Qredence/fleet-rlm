"""Daytona-backed interpreter compatible with the shared ReAct + RLM runtime."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

import dspy
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.core.execution.interpreter_events import (
    complete_event_data,
    emit_execution_event,
    start_event_data,
    summarize_code,
)
from fleet_rlm.core.execution.profiles import ExecutionProfile
from fleet_rlm.core.tools.llm_tools import LLMQueryMixin

from .chat_state import dedupe_paths, normalized_context_sources
from .protocol import ExecutionEventFrame, HostCallbackRequest, HostCallbackResponse
from .sandbox import DaytonaSandboxRuntime, DaytonaSandboxSession
from .sdk import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH


def _run_async_compat(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except Exception as exc:  # pragma: no cover - thread boundary
            error.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


class DaytonaInterpreter(LLMQueryMixin):
    """Stateful Daytona interpreter that plugs into canonical ``dspy.RLM`` flows."""

    def __init__(
        self,
        *,
        runtime: DaytonaSandboxRuntime | None = None,
        timeout: int = 900,
        execute_timeout: int | None = None,
        volume_name: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        delete_session_on_shutdown: bool = True,
        sub_lm: dspy.LM | None = None,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
        async_execute: bool = True,
    ) -> None:
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.timeout = timeout
        self.execute_timeout = execute_timeout or timeout
        self.volume_name = volume_name
        self.volume_mount_path = str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH)
        self.repo_url = repo_url
        self.repo_ref = repo_ref
        self.context_paths = dedupe_paths(list(context_paths or []))
        self.delete_session_on_shutdown = delete_session_on_shutdown
        self.default_execution_profile = default_execution_profile
        self.async_execute = async_execute

        self.sub_lm = sub_lm
        self.max_llm_calls = max_llm_calls
        self.llm_call_timeout = llm_call_timeout
        self._llm_call_count = 0
        self._llm_call_lock = threading.Lock()
        self._sub_lm_executor = None
        self._sub_lm_executor_lock = threading.Lock()

        self.output_fields: list[dict[str, Any]] | None = None
        self._tools: dict[str, Callable[..., Any]] = {}
        self.execution_event_callback: Callable[[dict[str, Any]], None] | None = None
        self._volume = None

        self._started = False
        self._session: DaytonaSandboxSession | None = None
        self._session_source_key: (
            tuple[str | None, str | None, tuple[str, ...], str | None] | None
        ) = None
        self._persisted_sandbox_id: str | None = None
        self._persisted_workspace_path: str | None = None
        self._persisted_context_sources: list[Any] = []

    def __enter__(self) -> "DaytonaInterpreter":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        self.shutdown()
        return False

    async def __aenter__(self) -> "DaytonaInterpreter":
        await self.astart()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        _ = (exc_type, exc_val, exc_tb)
        await self.ashutdown()
        return False

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    @tools.setter
    def tools(self, value: dict[str, Callable[..., Any]]) -> None:
        self._tools = value

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        normalized_repo_url = str(repo_url or "").strip() or None
        normalized_repo_ref = str(repo_ref or "").strip() or None
        normalized_context_paths = dedupe_paths(list(context_paths or []))
        normalized_volume = str(volume_name or "").strip() or None
        source_key = (
            normalized_repo_url,
            normalized_repo_ref,
            tuple(normalized_context_paths),
            normalized_volume,
        )

        if force_new_session or (
            self._session is not None and self._session_source_key != source_key
        ):
            self._detach_session(delete=True)

        self.repo_url = normalized_repo_url
        self.repo_ref = normalized_repo_ref
        self.context_paths = normalized_context_paths
        self.volume_name = normalized_volume

    def export_session_state(self) -> dict[str, Any]:
        self._persist_session_snapshot()
        context_sources = (
            list(self._session.context_sources)
            if self._session is not None
            else list(self._persisted_context_sources)
        )
        return {
            "daytona": {
                "repo_url": self.repo_url,
                "repo_ref": self.repo_ref,
                "context_paths": list(self.context_paths),
                "sandbox_id": (
                    self._session.sandbox_id
                    if self._session is not None
                    else self._persisted_sandbox_id
                ),
                "workspace_path": (
                    self._session.workspace_path
                    if self._session is not None
                    else self._persisted_workspace_path
                ),
                "context_sources": [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in context_sources
                ],
            }
        }

    def import_session_state(self, state: dict[str, Any]) -> None:
        self._detach_session(delete=False)
        raw_daytona = state.get("daytona", {})
        daytona_state = raw_daytona if isinstance(raw_daytona, dict) else {}
        self.repo_url = str(daytona_state.get("repo_url", "") or "").strip() or None
        self.repo_ref = str(daytona_state.get("repo_ref", "") or "").strip() or None
        self.context_paths = dedupe_paths(
            [str(item) for item in daytona_state.get("context_paths", []) or []]
        )
        self._persisted_sandbox_id = (
            str(daytona_state.get("sandbox_id", "") or "").strip() or None
        )
        self._persisted_workspace_path = (
            str(daytona_state.get("workspace_path", "") or "").strip() or None
        )
        self._persisted_context_sources = normalized_context_sources(
            daytona_state.get("context_sources", [])
        )
        self._session_source_key = None

    def start(self) -> None:
        if self._started:
            return
        self._ensure_session().start_driver(timeout=float(self.execute_timeout))
        self._started = True

    async def astart(self) -> None:
        if self._started:
            return
        session = await self._aensure_session()
        await session.astart_driver(timeout=float(self.execute_timeout))
        self._started = True

    def shutdown(self) -> None:
        self._detach_session(delete=self.delete_session_on_shutdown)
        self._started = False

    async def ashutdown(self) -> None:
        await self._adetach_session(delete=self.delete_session_on_shutdown)
        self._started = False

    @contextmanager
    def execution_profile(self, profile: ExecutionProfile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def build_delegate_child(
        self, *, remaining_llm_budget: int
    ) -> "DaytonaInterpreter":
        runtime = DaytonaSandboxRuntime(config=self.runtime._resolved_config)
        child = DaytonaInterpreter(
            runtime=runtime,
            timeout=self.timeout,
            execute_timeout=self.execute_timeout,
            volume_name=self.volume_name,
            repo_url=self.repo_url,
            repo_ref=self.repo_ref,
            context_paths=list(self.context_paths),
            delete_session_on_shutdown=True,
            sub_lm=self.sub_lm,
            max_llm_calls=remaining_llm_budget,
            llm_call_timeout=self.llm_call_timeout,
            default_execution_profile=ExecutionProfile.RLM_DELEGATE,
            async_execute=self.async_execute,
        )
        setattr(
            child,
            "_check_and_increment_llm_calls",
            self._check_and_increment_llm_calls,
        )
        return child

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        return _run_async_compat(
            self.aexecute(
                code,
                variables=variables,
                execution_profile=execution_profile,
            )
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        session = await self._aensure_session()
        await session.astart_driver(timeout=float(self.execute_timeout))

        safe_vars: dict[str, Any] = {}
        for key, value in (variables or {}).items():
            try:
                json.dumps(value)
                safe_vars[str(key)] = value
            except TypeError:
                safe_vars[str(key)] = str(value)

        profile = execution_profile or self.default_execution_profile
        profile_value = profile.value if hasattr(profile, "value") else str(profile)
        code_hash, code_preview = summarize_code(code)
        started_at = time.time()
        emit_execution_event(
            self,
            start_event_data(
                execution_profile=str(profile_value),
                code_hash=code_hash,
                code_preview=code_preview,
            ),
        )

        try:
            response = await session.aexecute_code(
                code=code,
                variables=safe_vars,
                tool_names=self._tool_names(),
                callback_handler=self._handle_host_callback,
                timeout=float(self.execute_timeout),
                submit_schema=self.output_fields,
                execution_profile=str(profile_value),
                progress_handler=lambda frame: self._handle_progress_frame(
                    frame,
                    code_hash=code_hash,
                    code_preview=code_preview,
                    execution_profile=str(profile_value),
                ),
            )
        except Exception as exc:
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=str(profile_value),
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="exception",
                    error_type=type(exc).__name__,
                    error=str(exc),
                ),
            )
            raise CodeInterpreterError(str(exc)) from exc

        final_payload = None
        if isinstance(response.final_artifact, dict):
            final_payload = response.final_artifact.get("value")
        stdout_preview = str(response.stdout or "")
        stderr_preview = str(response.stderr or "")

        if response.error:
            error_text = str(response.error)
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=str(profile_value),
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="stderr",
                    stdout_preview=stdout_preview or None,
                    stderr_preview=stderr_preview or None,
                    error_type="ExecutionError",
                    error=error_text,
                ),
            )
            combined = stdout_preview.strip()
            if combined and error_text:
                return f"{combined}\n{error_text}"
            return error_text

        if final_payload is not None:
            output_keys = (
                list(final_payload.keys())[:50]
                if isinstance(final_payload, dict)
                else None
            )
            emit_execution_event(
                self,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=str(profile_value),
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=True,
                    result_kind="final_output",
                    output_keys=output_keys,
                    stdout_preview=stdout_preview or None,
                    stderr_preview=stderr_preview or None,
                ),
            )
            return FinalOutput(final_payload)

        emit_execution_event(
            self,
            complete_event_data(
                started_at=started_at,
                execution_profile=str(profile_value),
                code_hash=code_hash,
                code_preview=code_preview,
                success=not bool(stderr_preview),
                result_kind="stderr" if stderr_preview else "stdout",
                stdout_preview=stdout_preview or None,
                stderr_preview=stderr_preview or None,
            ),
        )
        if stderr_preview:
            combined = stdout_preview.strip()
            if combined:
                return f"{combined}\n{stderr_preview}"
            return stderr_preview
        return stdout_preview

    def _tool_names(self) -> list[str]:
        tools = ["llm_query", "llm_query_batched", "rlm_query", "rlm_query_batched"]
        if self._tools:
            tools.extend(self._tools.keys())
        return list(dict.fromkeys(tools))

    def _handle_host_callback(
        self, request: HostCallbackRequest
    ) -> HostCallbackResponse:
        payload = request.payload or {}
        name = str(request.name or "").strip()

        try:
            if name == "llm_query":
                task = payload.get("task")
                args = (
                    payload.get("args") if isinstance(payload.get("args"), list) else []
                )
                prompt = task if task is not None else (args[0] if args else "")
                value = self.llm_query(str(prompt))
            elif name == "llm_query_batched":
                raw_tasks = payload.get("tasks")
                args = (
                    payload.get("args") if isinstance(payload.get("args"), list) else []
                )
                prompts = (
                    raw_tasks
                    if isinstance(raw_tasks, list)
                    else (args[0] if args else [])
                )
                if not isinstance(prompts, list):
                    prompts = []
                value = self.llm_query_batched([str(item) for item in prompts])
            elif name in self._tools:
                raw_args = payload.get("args")
                args: list[Any] = raw_args if isinstance(raw_args, list) else []
                raw_kwargs = payload.get("kwargs")
                kwargs: dict[str, Any] = (
                    {str(key): value for key, value in raw_kwargs.items()}
                    if isinstance(raw_kwargs, dict)
                    else {}
                )
                value = self._tools[name](*args, **kwargs)
            else:
                raise RuntimeError(f"Unknown host callback: {name}")

            try:
                json.dumps(value)
                response_value = value
            except TypeError:
                response_value = str(value)
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=True,
                value=response_value,
            )
        except Exception as exc:
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _handle_progress_frame(
        self,
        frame: ExecutionEventFrame,
        *,
        code_hash: str,
        code_preview: str,
        execution_profile: str,
    ) -> None:
        callback = self.execution_event_callback
        if callback is None:
            return
        try:
            callback(
                {
                    "phase": "progress",
                    "timestamp": time.time(),
                    "execution_profile": execution_profile,
                    "code_hash": code_hash,
                    "code_preview": code_preview,
                    "stream": frame.stream,
                    "text": frame.text,
                    "truncated": frame.truncated,
                }
            )
        except Exception:
            return

    async def _aensure_session(self) -> DaytonaSandboxSession:
        source_key = (
            self.repo_url,
            self.repo_ref,
            tuple(self.context_paths),
            self.volume_name,
        )
        if self._session is not None and self._session_source_key == source_key:
            return self._session

        if self._session is not None:
            await self._adetach_session(delete=True)

        if (
            self._persisted_sandbox_id
            and self._persisted_workspace_path
            and self._session_source_key in {None, source_key}
        ):
            try:
                self._session = await self.runtime.aresume_workspace_session(
                    sandbox_id=self._persisted_sandbox_id,
                    repo_url=self.repo_url,
                    ref=self.repo_ref,
                    workspace_path=self._persisted_workspace_path,
                    context_sources=self._persisted_context_sources,
                )
                self._session_source_key = source_key
                self._persist_session_snapshot()
                return self._session
            except Exception:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []

        self._session = await self.runtime.acreate_workspace_session(
            repo_url=self.repo_url,
            ref=self.repo_ref,
            context_paths=list(self.context_paths),
            volume_name=self.volume_name,
        )
        self._session_source_key = source_key
        self._persist_session_snapshot()
        return self._session

    def _ensure_session(self) -> DaytonaSandboxSession:
        return _run_async_compat(self._aensure_session())

    def _persist_session_snapshot(
        self, session: DaytonaSandboxSession | None = None
    ) -> None:
        active_session = session or self._session
        if active_session is None:
            return
        self._persisted_sandbox_id = active_session.sandbox_id
        self._persisted_workspace_path = active_session.workspace_path
        self._persisted_context_sources = list(active_session.context_sources)

    async def _adetach_session(self, *, delete: bool) -> None:
        if self._session is None:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
            return

        active_session = self._session
        self._persist_session_snapshot(active_session)
        try:
            if delete:
                await active_session.adelete()
            else:
                await active_session.aclose_driver()
        finally:
            if delete:
                self._persisted_sandbox_id = None
                self._persisted_workspace_path = None
                self._persisted_context_sources = []
            self._session = None
            self._session_source_key = None

    def _detach_session(self, *, delete: bool) -> None:
        _run_async_compat(self._adetach_session(delete=delete))
