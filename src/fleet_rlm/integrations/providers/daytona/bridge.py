"""Minimal Daytona host-callback bridge for ``dspy.RLM`` interpreters."""

from __future__ import annotations

import inspect
import json
import keyword
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from dspy.primitives import CodeInterpreterError

_BROKER_PORT = 3000
_BROKER_SERVER_PATH = "/home/daytona/broker_server.py"
_BROKER_SESSION_COMMAND = (
    f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"
)
_BROKER_SERVER_CODE = """
\"\"\"Broker server for mediating tool calls between sandbox code and the host.\"\"\"

import json
import threading
import time
import uuid

from flask import Flask, jsonify, request

app = Flask(__name__)
_lock = threading.Lock()
_pending_requests: dict[str, dict[str, object]] = {}
_results: dict[str, object] = {}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/tool_call", methods=["POST"])
def tool_call():
    data = request.json or {}
    call_id = str(data.get("id") or uuid.uuid4())
    tool_name = str(data.get("tool_name") or "")
    args = data.get("args", [])
    kwargs = data.get("kwargs", {})

    with _lock:
        _pending_requests[call_id] = {
            "tool_name": tool_name,
            "args": args if isinstance(args, list) else [],
            "kwargs": kwargs if isinstance(kwargs, dict) else {},
            "claimed": False,
            "claimed_at": None,
            "lease_token": None,
        }

    timeout = 120.0
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        with _lock:
            if call_id in _results:
                result = _results.pop(call_id)
                _pending_requests.pop(call_id, None)
                return jsonify({"result": result})
        time.sleep(interval)
        elapsed += interval

    with _lock:
        _pending_requests.pop(call_id, None)
    return jsonify({"error": "Tool call timeout"}), 504


@app.route("/pending", methods=["GET"])
def get_pending():
    try:
        max_items = int(request.args.get("max", "1"))
    except ValueError:
        max_items = 1
    max_items = max(1, max_items)

    try:
        lease_seconds = float(request.args.get("lease_seconds", "60"))
    except ValueError:
        lease_seconds = 60.0
    lease_seconds = max(1.0, lease_seconds)

    requests_out = []
    with _lock:
        now = time.time()
        for call_id, payload in _pending_requests.items():
            if len(requests_out) >= max_items:
                break
            if call_id in _results:
                continue
            claimed_at = payload.get("claimed_at")
            if payload.get("claimed") and isinstance(claimed_at, (int, float)):
                if now - claimed_at < lease_seconds:
                    continue
            claim_token = str(uuid.uuid4())
            payload["claimed"] = True
            payload["claimed_at"] = now
            payload["lease_token"] = claim_token
            requests_out.append(
                {
                    "id": call_id,
                    "tool_name": payload["tool_name"],
                    "args": payload["args"],
                    "kwargs": payload["kwargs"],
                    "claim_token": claim_token,
                }
            )
    return jsonify({"requests": requests_out})


@app.route("/result/<call_id>", methods=["POST"])
def post_result(call_id: str):
    data = request.json or {}
    result = data.get("result")
    claim_token = data.get("claim_token")
    with _lock:
        req = _pending_requests.get(call_id)
        if req is None:
            return jsonify({"error": "Unknown or expired call_id"}), 404
        expected_token = req.get("lease_token")
        if not expected_token or claim_token != expected_token:
            return jsonify({"error": "Stale or invalid claim token"}), 409
        _results[call_id] = result
        req["lease_token"] = None
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, threaded=True)
""".strip()

_TOOL_WRAPPER_TEMPLATE = """
def {tool_name}({signature}):
    \"\"\"Wrapper for {tool_name} tool - calls host via broker.\"\"\"
    import json as _json
    import urllib.request as _urllib_request
    import uuid as _uuid

    call_id = str(_uuid.uuid4())
    payload = _json.dumps(
        {{
            "id": call_id,
            "tool_name": "{tool_name}",
            "args": [{args_list}],
            "kwargs": {{{kwargs_dict}}},
        }}
    ).encode("utf-8")
    req = _urllib_request.Request(
        "http://localhost:{broker_port}/tool_call",
        data=payload,
        headers={{"Content-Type": "application/json"}},
        method="POST",
    )
    with _urllib_request.urlopen(req, timeout=130) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"Tool call failed: {{data['error']}}")
    result = data.get("result")
    if isinstance(result, str):
        try:
            return _json.loads(result)
        except (ValueError, _json.JSONDecodeError):
            return result
    return result
""".strip()


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

    def ensure_started(self) -> None:
        if self._broker_url is not None:
            return
        self.sandbox.fs.upload_file(
            _BROKER_SERVER_CODE.encode("utf-8"),
            _BROKER_SERVER_PATH,
        )
        from daytona import SessionExecuteRequest

        session_id = f"broker-{uuid.uuid4().hex[:8]}"
        self.sandbox.process.create_session(session_id)
        self.sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(
                command=_BROKER_SESSION_COMMAND,
                run_async=True,
            ),
        )
        preview = self.sandbox.get_preview_link(_BROKER_PORT)
        self._broker_session_id = session_id
        self._broker_url = str(preview.url).rstrip("/")
        self._broker_token = str(getattr(preview, "token", "") or "")
        self._wait_for_health()

    def sync_tools(self, tools: dict[str, Callable[..., Any]]) -> None:
        if not tools:
            return
        self.ensure_started()
        for tool_name, tool_func in tools.items():
            if tool_name in self._injected_tools:
                continue
            if not tool_name.isidentifier() or keyword.iskeyword(tool_name):
                raise CodeInterpreterError(f"Invalid tool name: '{tool_name}'")
            wrapper_code = self._generate_tool_wrapper(tool_name, tool_func)
            result = self.sandbox.code_interpreter.run_code(
                wrapper_code,
                context=self.context,
            )
            if result.error:
                raise CodeInterpreterError(
                    f"Failed to inject tool '{tool_name}': {result.error.value}"
                )
            self._injected_tools.add(tool_name)

    def execute(
        self,
        *,
        code: str,
        timeout: int,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> DaytonaBridgeExecution:
        self.ensure_started()
        execution_result: dict[str, Any] = {
            "result": None,
            "error": None,
            "stdout": "",
            "stderr": "",
        }
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        code_done_event = threading.Event()

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

        def _run_code() -> None:
            try:
                execution_result["result"] = self.sandbox.code_interpreter.run_code(
                    code,
                    context=self.context,
                    on_stdout=_handle_stdout,
                    on_stderr=_handle_stderr,
                    timeout=timeout,
                )
            except Exception as exc:  # pragma: no cover - thread boundary
                execution_result["error"] = exc
            finally:
                execution_result["stdout"] = "".join(stdout_parts)
                execution_result["stderr"] = "".join(stderr_parts)
                code_done_event.set()

        code_thread = threading.Thread(target=_run_code, daemon=True)
        code_thread.start()
        callback_count = self._poll_and_execute_tools(
            code_done_event=code_done_event,
            tool_executor=tool_executor,
        )
        code_thread.join()
        error = execution_result.get("error")
        if error is not None:
            raise error
        return DaytonaBridgeExecution(
            result=execution_result["result"],
            stdout=str(execution_result["stdout"] or ""),
            stderr=str(execution_result["stderr"] or ""),
            callback_count=callback_count,
        )

    def close(self) -> None:
        session_id = self._broker_session_id
        self._broker_url = None
        self._broker_token = None
        self._broker_session_id = None
        self._injected_tools.clear()
        if not session_id:
            return
        try:
            self.sandbox.process.delete_session(session_id)
        except Exception:
            return

    def _wait_for_health(self, timeout: float = 30.0) -> None:
        broker_url = self._broker_url
        if broker_url is None:
            raise CodeInterpreterError("Broker URL was not initialized.")
        started = time.time()
        while time.time() - started < timeout:
            try:
                request = urllib.request.Request(
                    f"{broker_url}/health",
                    headers=self._preview_headers(),
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                time.sleep(0.5)
                continue
        raise CodeInterpreterError("Broker server failed to start within timeout")

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
        signature = inspect.signature(tool_func)
        params = list(signature.parameters.values())
        sig_parts: list[str] = []
        args_list: list[str] = []
        kwargs_parts: list[str] = []
        added_kw_only_separator = False
        last_positional_only_idx = max(
            (
                index
                for index, param in enumerate(params)
                if param.kind == inspect.Parameter.POSITIONAL_ONLY
            ),
            default=-1,
        )

        def _format_param(param: inspect.Parameter) -> str:
            if param.default is inspect.Parameter.empty:
                return param.name
            return f"{param.name}={repr(param.default)}"

        for index, param in enumerate(params):
            if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                sig_parts.append(_format_param(param))
                args_list.append(param.name)
                if index == last_positional_only_idx:
                    sig_parts.append("/")
                continue
            if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD:
                sig_parts.append(_format_param(param))
                if param.default is inspect.Parameter.empty:
                    args_list.append(param.name)
                else:
                    kwargs_parts.append(f'"{param.name}": {param.name}')
                continue
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                sig_parts.append(f"*{param.name}")
                args_list.append(f"*{param.name}")
                added_kw_only_separator = True
                continue
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                if not added_kw_only_separator:
                    sig_parts.append("*")
                    added_kw_only_separator = True
                sig_parts.append(_format_param(param))
                kwargs_parts.append(f'"{param.name}": {param.name}')
                continue
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                sig_parts.append(f"**{param.name}")
                kwargs_parts.append(f"**{param.name}")
                continue
            raise CodeInterpreterError(
                f"Unsupported parameter kind for tool '{tool_name}': {param.kind}"
            )

        return _TOOL_WRAPPER_TEMPLATE.format(
            tool_name=tool_name,
            signature=", ".join(sig_parts),
            args_list=", ".join(args_list),
            kwargs_dict=", ".join(kwargs_parts),
            broker_port=_BROKER_PORT,
        )

    def _poll_and_execute_tools(
        self,
        *,
        code_done_event: threading.Event,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> int:
        broker_url = self._broker_url
        if broker_url is None:
            return 0

        callback_count = 0
        inflight: dict[str, Future[None]] = {}

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

        def _execute_one(pending: dict[str, Any]) -> None:
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
                result = tool_executor(tool_name, safe_args, safe_kwargs)
            except Exception as exc:  # pragma: no cover - host callback boundary
                result = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                _post_result(call_id, result, claim_token)
            except urllib.error.HTTPError as exc:
                if exc.code not in {404, 409}:
                    raise

        with ThreadPoolExecutor(max_workers=self.max_concurrent_tool_calls) as executor:
            while not code_done_event.is_set() or inflight:
                for call_id, future in list(inflight.items()):
                    if not future.done():
                        continue
                    inflight.pop(call_id, None)
                    future.result()
                    callback_count += 1

                if code_done_event.is_set():
                    time.sleep(0.01)
                    continue

                capacity = self.max_concurrent_tool_calls - len(inflight)
                if capacity <= 0:
                    time.sleep(0.05)
                    continue

                try:
                    for pending in _fetch_pending(capacity):
                        call_id = str(pending.get("id") or "")
                        if not call_id or call_id in inflight:
                            continue
                        inflight[call_id] = executor.submit(_execute_one, pending)
                except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                    time.sleep(0.05)
                    continue

                time.sleep(0.05)

        return callback_count


__all__ = ["DaytonaBridgeExecution", "DaytonaToolBridge"]
