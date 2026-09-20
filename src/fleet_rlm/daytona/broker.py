# ruff: noqa: E501
"""Sandbox-local HTTP bridge for Daytona interpreter host tools.

The broker owns the only executable namespace used by a live invocation.  The
Fleet process only polls typed requests and fulfils registered host tools; it
never executes model-authored Python.
"""

from __future__ import annotations

import inspect
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from fleet_rlm.daytona.errors import DaytonaAdapterError, sanitize_provider_message
from fleet_rlm.json_types import validate_json_value
from fleet_rlm.rlm.events import _resolve_awaitable_result

logger = logging.getLogger(__name__)

_SERVER_PATH = "/home/daytona/fleet_rlm_tool_broker.py"
_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_CHARS = 64 * 1024
_DEFAULT_TOOL_TIMEOUT_S = 120


_SERVER_SOURCE = r"""
import contextlib, hmac, io, json, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

_secret = __SECRET__
_pending, _results, _completed, _namespace = {}, {}, set(), {"__name__": "__fleet_rlm_repl__"}
_lock, _execution_lock = threading.Lock(), threading.Lock()
_active_deadline = None

# Keep a bounded, useful conversion ceiling for legitimate high-precision
# computations.  The default CPython limit makes the Pi canary fail before it
# can submit a result, despite the sandbox output budget already bounding what
# returns to the host.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(200_000)

class _BoundedWriter(io.StringIO):
    def __init__(self, limit):
        super().__init__()
        self._limit = limit
        self._truncated = False
    def write(self, value):
        remaining = self._limit - self.tell()
        if remaining <= 0:
            self._truncated = True
            return len(value)
        if len(value) > remaining:
            super().write(value[:remaining])
            self._truncated = True
        else:
            super().write(value)
        return len(value)
    def getvalue(self):
        value = super().getvalue()
        return value + "\n...[sandbox output truncated]" if self._truncated else value

def _send(handler, body, status=200):
    raw = json.dumps(body, allow_nan=False).encode("utf-8")
    handler.send_response(status); handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw))); handler.end_headers(); handler.wfile.write(raw)

def _read(handler):
    size = int(handler.headers.get("Content-Length", "0"))
    if size > __MAX_REQUEST_BYTES__: raise ValueError("request too large")
    return json.loads(handler.rfile.read(size).decode("utf-8")) if size else {}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def _authorized(self): return hmac.compare_digest(self.headers.get("X-Broker-Secret", ""), _secret)
    def do_GET(self):
        if self.path == "/health": _send(self, {"status": "ok"}); return
        if not self._authorized(): _send(self, {"error": "unauthorized"}, 401); return
        if self.path.startswith("/pending"):
            out = []
            with _lock:
                for call_id, request in list(_pending.items()):
                    if request["lease"] is not None: continue
                    request["lease"] = uuid.uuid4().hex
                    out.append({"id": call_id, "lease": request["lease"], "tool_name": request["tool_name"], "args": request["args"], "kwargs": request["kwargs"]})
            _send(self, {"requests": out}); return
        _send(self, {"error": "not found"}, 404)
    def do_POST(self):
        if not self._authorized(): _send(self, {"error": "unauthorized"}, 401); return
        try: data = _read(self)
        except Exception: _send(self, {"error": "invalid request"}, 400); return
        if self.path == "/reset":
            new_secret = data.get("secret")
            if not isinstance(new_secret, str) or len(new_secret) < 32:
                _send(self, {"error": "invalid invocation"}, 400); return
            if not _execution_lock.acquire(blocking=False):
                _send(self, {"error": "execution active"}, 409); return
            try:
                with _lock:
                    if _pending:
                        _send(self, {"error": "tool calls active"}, 409); return
                    global _secret
                    _secret = new_secret
                    _namespace.clear(); _namespace["__name__"] = "__fleet_rlm_repl__"
                    _results.clear(); _completed.clear()
            finally:
                _execution_lock.release()
            _send(self, {"status": "ok"}); return
        if self.path == "/execute":
            code, variables = data.get("code"), data.get("variables") or {}
            timeout_s = data.get("timeout_s", __DEFAULT_TOOL_TIMEOUT_S__)
            if (not isinstance(code, str) or not isinstance(variables, dict) or isinstance(timeout_s, bool)
                    or not isinstance(timeout_s, (int, float)) or timeout_s <= 0):
                _send(self, {"error": "invalid execution"}, 400); return
            stdout, stderr = _BoundedWriter(__MAX_OUTPUT_CHARS__), _BoundedWriter(__MAX_OUTPUT_CHARS__)
            result = {"stdout": "", "stderr": "", "final": None, "error": None, "error_category": None, "tool_error": None}
            try:
                with _execution_lock:
                    with _lock:
                        global _active_deadline
                        _active_deadline = time.monotonic() + float(timeout_s)
                        _namespace["_fleet_tool_timeout_s"] = float(timeout_s)
                    try:
                        _namespace.update(variables)
                        try:
                            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr): exec(compile(code, "<fleet-rlm>", "exec"), _namespace, _namespace)
                        except BaseException as exc:
                            value = getattr(exc, "value", None)
                            if type(exc).__name__ == "FleetFinalOutputError" and isinstance(value, dict): result["final"] = value
                            else:
                                failure = getattr(exc, "fleet_tool_error", None)
                                if isinstance(failure, dict):
                                    result["tool_error"] = failure
                                    result["error"] = failure["message"]
                                    result["error_category"] = failure["category"]
                                else:
                                    result.update(error=str(exc)[:2000], error_category=type(exc).__name__)
                    finally:
                        with _lock:
                            _active_deadline = None
            finally:
                result["stdout"], result["stderr"] = stdout.getvalue(), stderr.getvalue()
            _send(self, result); return
        if self.path == "/tool_call":
            call_id = str(data.get("id") or uuid.uuid4().hex); event = threading.Event()
            with _lock:
                if call_id in _pending or call_id in _results or call_id in _completed:
                    _send(self, {"error": "duplicate call"}, 409); return
                if _active_deadline is None or time.monotonic() >= _active_deadline:
                    _send(self, {"error": "invocation is not executing"}, 409); return
                _pending[call_id] = {"tool_name": data.get("tool_name"), "args": data.get("args") or [], "kwargs": data.get("kwargs") or {}, "lease": None, "event": event}
                deadline = _active_deadline
            wait_s = max(0.0, deadline - time.monotonic()) if deadline is not None else __DEFAULT_TOOL_TIMEOUT_S__
            if not event.wait(wait_s):
                with _lock:
                    _pending.pop(call_id, None); _results.pop(call_id, None); _completed.add(call_id)
                _send(self, {"error": "tool call timed out"}, 504); return
            with _lock:
                result = _results.pop(call_id, {"tool_error": {"category": "missing_result", "message": "tool result unavailable", "call_id": call_id}})
                _pending.pop(call_id, None); _completed.add(call_id)
            _send(self, result); return
        if self.path == "/result":
            call_id, lease = str(data.get("id") or ""), data.get("lease")
            with _lock:
                request = _pending.get(call_id)
                if request is None:
                    duplicate = call_id in _completed
                    _send(self, {"error": "duplicate call" if duplicate else "unknown call"}, 409 if duplicate else 404); return
                if request["lease"] != lease: _send(self, {"error": "stale lease"}, 409); return
                _results[call_id] = {"tool_error": data["tool_error"]} if "tool_error" in data else {"result": data.get("result")}
                request["event"].set()
            _send(self, {"status": "ok"}); return
        _send(self, {"error": "not found"}, 404)

class Server(ThreadingMixIn, HTTPServer): daemon_threads = True
Server(("0.0.0.0", __PORT__), Handler).serve_forever()
"""


class DaytonaHttpToolBroker:
    """Run a local sandbox broker and fulfil its JSON-only tool requests."""

    def __init__(self, sandbox: Any, *, port: int, async_bridge: Any | None = None) -> None:
        self._sandbox = sandbox
        self._port = port
        self._secret = secrets.token_urlsafe(32)
        self._url: str | None = None
        self._session: str | None = None
        self._client: httpx.Client | None = None
        self._tools: dict[str, Callable[..., Any]] = {}
        self._async_bridge = async_bridge
        self._stopped = False
        self._delivery_error: DaytonaAdapterError | None = None

    def bind_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        if self._url is not None:
            raise DaytonaAdapterError(
                message="tool bindings changed after broker startup", cause_type="BrokerBindingError"
            )
        self._tools = dict(tools)

    def reset_invocation(self) -> None:
        """Clear settled execution state and rotate the invocation credential."""
        if self._client is None:
            return
        secret = secrets.token_urlsafe(32)
        response = self._client.post("/reset", json={"secret": secret})
        if response.status_code != 200:
            raise DaytonaAdapterError(message="broker invocation is still active", cause_type="BrokerBindingError")
        self._secret = secret
        self._client.headers["X-Broker-Secret"] = secret
        self._delivery_error = None

    def rebind_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        """Refresh the host registry between executions on a live broker."""
        if self._stopped:
            raise DaytonaAdapterError(message="broker is stopped", cause_type="InterpreterLifecycleError")
        self._tools = dict(tools)

    def bind_async_bridge(self, async_bridge: Any | None) -> None:
        """Bind the composition-owned bridge before broker startup."""
        if self._url is not None:
            raise DaytonaAdapterError(
                message="async bridge changed after broker startup", cause_type="BrokerBindingError"
            )
        self._async_bridge = async_bridge

    def rebind_async_bridge(self, async_bridge: Any | None) -> None:
        """Refresh the composition-owned bridge between executions."""
        if self._stopped:
            raise DaytonaAdapterError(message="broker is stopped", cause_type="InterpreterLifecycleError")
        self._async_bridge = async_bridge

    def setup_source(self, submit_source: str) -> str:
        return (
            "class _FleetToolCallError(RuntimeError):\n"
            "    def __init__(self, failure):\n"
            "        self.fleet_tool_error = {\n"
            "            'category': str(failure.get('category', 'tool_error'))[:80],\n"
            "            'message': str(failure.get('message', 'tool call failed'))[:500],\n"
            "            'call_id': str(failure.get('call_id', ''))[:128],\n"
            "        }\n"
            "        super().__init__(self.fleet_tool_error['message'])\n\n"
            + "\n\n".join((self._wrapper_source(name, fn) for name, fn in self._tools.items()))
            + "\n\n"
            + submit_source
        )

    def execute(self, code: str, variables: Mapping[str, Any], *, timeout_s: int) -> Any:
        self._ensure_started()
        assert self._client is not None
        self._delivery_error = None
        client = self._client
        outcome: list[httpx.Response | BaseException] = []

        def post() -> None:
            try:
                outcome.append(
                    client.post(
                        "/execute",
                        json={"code": code, "variables": dict(variables), "timeout_s": timeout_s},
                        timeout=timeout_s,
                    )
                )
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=post, daemon=True)
        worker.start()
        while worker.is_alive():
            if self._stopped:
                break
            self._poll_once()
            worker.join(0.05)
        # The remote /execute request owns every outstanding /tool_call.  Do
        # not return (or tear down its broker) until that request has settled,
        # even after a rejected result delivery.  The typed delivery failure is
        # raised only once remote execution has contained its waiting call.
        if self._delivery_error is not None:
            raise self._delivery_error
        if not outcome or isinstance(outcome[0], BaseException):
            raise DaytonaAdapterError(message="sandbox execution request failed", cause_type="BrokerExecutionError")
        response = outcome[0]
        if response.status_code != 200:
            raise DaytonaAdapterError(message="sandbox execution failed", cause_type="BrokerExecutionError")
        return response.json()

    def stop(self, *, strict: bool = False) -> None:
        self._stopped = True
        client, self._client = self._client, None
        if client is not None:
            client.close()
        if self._session is not None:
            session, self._session = self._session, None
            try:
                self._sandbox.process.delete_session(session)
            except Exception:
                if strict:
                    raise

    def _ensure_started(self) -> None:
        if self._stopped:
            raise DaytonaAdapterError(message="broker is stopped", cause_type="InterpreterLifecycleError")
        if self._url is not None:
            return
        source = (
            _SERVER_SOURCE.replace("__SECRET__", repr(self._secret))
            .replace("__PORT__", str(self._port))
            .replace("__MAX_REQUEST_BYTES__", str(_MAX_REQUEST_BYTES))
            .replace("__MAX_OUTPUT_CHARS__", str(_MAX_OUTPUT_CHARS))
            .replace("__DEFAULT_TOOL_TIMEOUT_S__", str(_DEFAULT_TOOL_TIMEOUT_S))
        )
        try:
            from daytona import SessionExecuteRequest

            self._sandbox.fs.upload_file(source.encode(), _SERVER_PATH)
            self._session = f"fleet-tool-broker-{uuid.uuid4().hex[:8]}"
            self._sandbox.process.create_session(self._session)
            self._sandbox.process.execute_session_command(
                self._session, SessionExecuteRequest(command=f"python {_SERVER_PATH}", run_async=True)
            )
            preview = self._sandbox.get_preview_link(self._port)
            self._url = str(preview.url).rstrip("/")
            token = str(getattr(preview, "token", "") or "")
            headers = {"X-Broker-Secret": self._secret}
            if token:
                headers["X-Daytona-Preview-Token"] = token
            self._client = httpx.Client(base_url=self._url, headers=headers, timeout=5)
            for _ in range(20):
                if self._client.get("/health").status_code == 200:
                    return
                time.sleep(0.1)
        except Exception as exc:
            self.stop()
            raise DaytonaAdapterError(
                message="sandbox tool broker failed to start", cause_type="BrokerStartupError"
            ) from exc
        raise DaytonaAdapterError(message="sandbox tool broker did not become healthy", cause_type="BrokerStartupError")

    def _poll_once(self) -> None:
        client = self._client
        if self._stopped or client is None:
            return
        try:
            requests = client.get("/pending").json().get("requests", [])
        except (httpx.HTTPError, ValueError):
            return
        for request in requests:
            name = str(request.get("tool_name") or "")
            try:
                tool = self._tools[name]
                result = _resolve_awaitable_result(
                    tool(*list(request.get("args") or []), **dict(request.get("kwargs") or {})),
                    async_bridge=self._async_bridge,
                )
                validate_json_value(result, path=f"Tool {name} result")
                body = {"id": request["id"], "lease": request["lease"], "result": result}
            except Exception as exc:
                body = {
                    "id": request.get("id"),
                    "lease": request.get("lease"),
                    "tool_error": {
                        "category": type(exc).__name__[:80],
                        "message": sanitize_provider_message(str(exc))[:500],
                        "call_id": str(request.get("id") or ""),
                    },
                }
            try:
                if not self._stopped and self._client is client:
                    response = client.post("/result", json=body)
                    if response.status_code != 200:
                        self._record_delivery_failure(
                            request, phase="result_delivery", category=f"http_{response.status_code}"
                        )
            except httpx.HTTPError:
                self._record_delivery_failure(request, phase="result_delivery", category="http_error")

    def _record_delivery_failure(self, request: Mapping[str, Any], *, phase: str, category: str) -> None:
        """Retain a sanitized failed delivery outcome until remote execution settles."""
        call_id = str(request.get("id") or "")[:128]
        tool_name = str(request.get("tool_name") or "")[:80]
        logger.warning(
            "sandbox tool result delivery failed call_id=%s tool_name=%s phase=%s category=%s",
            call_id,
            tool_name,
            phase,
            category,
        )
        self._delivery_error = DaytonaAdapterError(
            message="sandbox tool result delivery failed",
            cause_type="BrokerDeliveryError",
        )

    def _wrapper_source(self, name: str, tool: Callable[..., Any]) -> str:
        if not name.isidentifier():
            raise DaytonaAdapterError(message="invalid tool name", cause_type="BrokerBindingError")
        parameters = inspect.signature(tool).parameters.values()
        signature, kwargs = [], []
        for parameter in parameters:
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            default = "" if parameter.default is inspect.Parameter.empty else f"={parameter.default!r}"
            signature.append(f"{parameter.name}{default}")
            kwargs.append(f"{parameter.name!r}: {parameter.name}")
        return f"""def {name}({", ".join(signature)}):
    import json as _json, urllib.request as _request, uuid as _uuid
    _payload = _json.dumps({{"id": _uuid.uuid4().hex, "tool_name": {name!r}, "args": [], "kwargs": {{{", ".join(kwargs)}}}}}).encode()
    _req = _request.Request("http://127.0.0.1:{self._port}/tool_call", data=_payload, headers={{"Content-Type": "application/json", "X-Broker-Secret": {self._secret!r}}}, method="POST")
    with _request.urlopen(_req, timeout=float(globals().get('_fleet_tool_timeout_s', 120))) as _response: _reply = _json.loads(_response.read())
    if "tool_error" in _reply:
        _failure = _reply["tool_error"]
        raise _FleetToolCallError(_failure)
    return _reply.get("result")"""
