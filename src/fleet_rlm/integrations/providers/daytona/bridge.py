"""Minimal Daytona host-callback bridge for ``dspy.RLM`` interpreters."""

from __future__ import annotations

import asyncio
import json
import keyword
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from dspy.primitives import CodeInterpreterError

from .bridge_assets import (
    _BROKER_PORT,
    _BROKER_SERVER_CODE,
    _BROKER_SERVER_PATH,
    _BROKER_SESSION_COMMAND,
    generate_tool_wrapper,
)
from .runtime import _await_if_needed, _run_async_compat


@dataclass(slots=True)
class DaytonaBridgeExecution:
    """Execution output captured while a sandbox callback bridge is active."""

    result: Any
    stdout: str
    stderr: str
    callback_count: int


class DaytonaToolBridge:
    """Guide-aligned broker bridge for Daytona-hosted RLM tool callbacks."""

    def __init__(
        self,
        *,
        sandbox: Any,
        context: Any,
        max_concurrent_tool_calls: int = 32,
        tool_claim_lease_seconds: float = 60.0,
    ) -> None:
        if max_concurrent_tool_calls < 1:
            raise ValueError("max_concurrent_tool_calls must be >= 1")
        if tool_claim_lease_seconds < 1:
            raise ValueError("tool_claim_lease_seconds must be >= 1")
        self.sandbox = sandbox
        self.context = context
        self.max_concurrent_tool_calls = max_concurrent_tool_calls
        self.tool_claim_lease_seconds = float(tool_claim_lease_seconds)
        self._broker_url: str | None = None
        self._broker_token: str | None = None
        self._broker_session_id: str | None = None
        self._injected_tools: set[str] = set()

    def bind_context(self, context: Any) -> None:
        self.context = context

    async def aensure_started(self) -> None:
        if self._broker_url is not None:
            return
        await _await_if_needed(
            self.sandbox.fs.upload_file(
                _BROKER_SERVER_CODE.encode("utf-8"),
                _BROKER_SERVER_PATH,
            )
        )
        from daytona import SessionExecuteRequest

        session_id = f"broker-{uuid.uuid4().hex[:8]}"
        await _await_if_needed(self.sandbox.process.create_session(session_id))
        await _await_if_needed(
            self.sandbox.process.execute_session_command(
                session_id,
                SessionExecuteRequest(
                    command=_BROKER_SESSION_COMMAND,
                    run_async=True,
                ),
            )
        )
        preview = await _await_if_needed(self.sandbox.get_preview_link(_BROKER_PORT))
        self._broker_session_id = session_id
        self._broker_url = str(preview.url).rstrip("/")
        self._broker_token = str(getattr(preview, "token", "") or "")
        await self._await_health()

    def ensure_started(self) -> None:
        _run_async_compat(self.aensure_started)

    async def async_tools(self, tools: dict[str, Callable[..., Any]]) -> None:
        if not tools:
            return
        await self.aensure_started()
        for tool_name, tool_func in tools.items():
            if tool_name in self._injected_tools:
                continue
            if not tool_name.isidentifier() or keyword.iskeyword(tool_name):
                raise CodeInterpreterError(f"Invalid tool name: '{tool_name}'")
            wrapper_code = self._generate_tool_wrapper(tool_name, tool_func)
            result = await _await_if_needed(
                self.sandbox.code_interpreter.run_code(
                    wrapper_code,
                    context=self.context,
                )
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to inject tool '{tool_name}': {result.error.value}"
                )
            self._injected_tools.add(tool_name)

    def sync_tools(self, tools: dict[str, Callable[..., Any]]) -> None:
        _run_async_compat(self.async_tools, tools)

    async def aexecute(
        self,
        *,
        code: str,
        timeout: int,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> DaytonaBridgeExecution:
        await self.aensure_started()
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def _handle_stdout(message: Any) -> None:
            text = str(getattr(message, "output", "") or "")
            stdout_parts.append(text)
            if on_stdout is not None and text:
                on_stdout(text)

        def _handle_stderr(message: Any) -> None:
            text = str(getattr(message, "output", "") or "")
            stderr_parts.append(text)
            if on_stderr is not None and text:
                on_stderr(text)

        execution_task = asyncio.create_task(
            _await_if_needed(
                self.sandbox.code_interpreter.run_code(
                    code,
                    context=self.context,
                    on_stdout=_handle_stdout,
                    on_stderr=_handle_stderr,
                    timeout=timeout,
                )
            )
        )
        try:
            callback_count = await self._apoll_and_execute_tools(
                code_task=execution_task,
                tool_executor=tool_executor,
            )
            result = await execution_task
        except Exception:
            if not execution_task.done():
                execution_task.cancel()
                try:
                    await execution_task
                except asyncio.CancelledError:
                    pass
            raise
        return DaytonaBridgeExecution(
            result=result,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            callback_count=callback_count,
        )

    def execute(
        self,
        *,
        code: str,
        timeout: int,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> DaytonaBridgeExecution:
        return _run_async_compat(
            self.aexecute,
            code=code,
            timeout=timeout,
            tool_executor=tool_executor,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )

    async def aclose(self) -> None:
        session_id = self._broker_session_id
        self._broker_url = None
        self._broker_token = None
        self._broker_session_id = None
        self._injected_tools.clear()
        if not session_id:
            return
        try:
            await _await_if_needed(self.sandbox.process.delete_session(session_id))
        except Exception:
            return

    def close(self) -> None:
        _run_async_compat(self.aclose)

    async def _await_health(self, timeout: float = 30.0) -> None:
        broker_url = self._broker_url
        if broker_url is None:
            raise CodeInterpreterError("Broker URL was not initialized.")
        started = time.time()
        while time.time() - started < timeout:
            try:
                if await asyncio.to_thread(self._check_health, broker_url):
                    return
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                await asyncio.sleep(0.5)
                continue
        raise CodeInterpreterError("Broker server failed to start within timeout")

    def _check_health(self, broker_url: str) -> bool:
        request = urllib.request.Request(
            f"{broker_url}/health",
            headers=self._preview_headers(),
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200

    def _preview_headers(self) -> dict[str, str]:
        token = self._broker_token
        if not token:
            return {}
        return {"x-daytona-preview-token": token}

    def _generate_tool_wrapper(
        self,
        tool_name: str,
        tool_func: Callable[..., Any],
    ) -> str:
        return generate_tool_wrapper(tool_name=tool_name, tool_func=tool_func)

    async def _apoll_and_execute_tools(
        self,
        *,
        code_task: asyncio.Task[Any],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> int:
        broker_url = self._broker_url
        if broker_url is None:
            return 0

        callback_count = 0
        inflight: dict[str, asyncio.Task[None]] = {}

        def _fetch_pending(max_items: int) -> list[dict[str, Any]]:
            request = urllib.request.Request(
                (
                    f"{broker_url}/pending?max={max_items}"
                    f"&lease_seconds={self.tool_claim_lease_seconds}"
                ),
                headers=self._preview_headers(),
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("requests"), list):
                return [
                    item
                    for item in payload["requests"]
                    if isinstance(item, dict) and item.get("id")
                ]
            return []

        def _post_result(call_id: str, result: Any, claim_token: str | None) -> None:
            encoded_result = result if isinstance(result, str) else json.dumps(result)
            payload = json.dumps(
                {"result": encoded_result, "claim_token": claim_token}
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{broker_url}/result/{call_id}",
                data=payload,
                headers={
                    **self._preview_headers(),
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5):
                return

        async def _execute_one(pending: dict[str, Any]) -> None:
            call_id = str(pending["id"])
            tool_name = str(pending.get("tool_name") or "")
            args = pending.get("args")
            kwargs = pending.get("kwargs")
            claim_token = str(pending.get("claim_token") or "")
            safe_args = list(args) if isinstance(args, list) else []
            safe_kwargs = (
                {str(key): value for key, value in kwargs.items()}
                if isinstance(kwargs, dict)
                else {}
            )
            try:
                result = await asyncio.to_thread(
                    tool_executor, tool_name, safe_args, safe_kwargs
                )
            except Exception as exc:  # pragma: no cover - host callback boundary
                result = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                await asyncio.to_thread(_post_result, call_id, result, claim_token)
            except urllib.error.HTTPError as exc:
                if exc.code not in {404, 409}:
                    raise

        while not code_task.done() or inflight:
            for call_id, task in list(inflight.items()):
                if not task.done():
                    continue
                inflight.pop(call_id, None)
                await task
                callback_count += 1

            if code_task.done():
                if inflight:
                    await asyncio.sleep(0.01)
                continue

            capacity = self.max_concurrent_tool_calls - len(inflight)
            if capacity <= 0:
                await asyncio.sleep(0.05)
                continue

            try:
                pending_items = await asyncio.to_thread(_fetch_pending, capacity)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                await asyncio.sleep(0.05)
                continue

            for pending in pending_items:
                call_id = str(pending.get("id") or "")
                if not call_id or call_id in inflight:
                    continue
                inflight[call_id] = asyncio.create_task(_execute_one(pending))

            await asyncio.sleep(0.05)

        return callback_count


__all__ = ["DaytonaBridgeExecution", "DaytonaToolBridge"]
