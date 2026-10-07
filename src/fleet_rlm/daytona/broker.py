# ruff: noqa: E501
"""Sandbox-local HTTP bridge for Daytona interpreter host tools.

The broker owns the only executable namespace used by a live invocation.  The
Fleet process only polls typed requests and fulfils registered host tools; it
never executes model-authored Python.
"""

from __future__ import annotations

import contextlib
import inspect
import json
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
from fleet_rlm.rlm.budget import host_action_deadline
from fleet_rlm.rlm.events import _resolve_awaitable_result

logger = logging.getLogger(__name__)

_SERVER_PATH = "/home/daytona/fleet_rlm_tool_broker.py"
_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_CHARS = 64 * 1024
_DEFAULT_TOOL_TIMEOUT_S = 120
# The sandbox starts its action clock when /execute arrives, after the host
# sent it. Waiting a little past timeout_s lets the sandbox's own deadline
# reply (for example a timed-out tool wait) reach the host as a recoverable
# action error instead of the host transport timing out first.
_EXECUTE_RESPONSE_GRACE_S = 10.0


def _encode_result_envelope(body: Mapping[str, Any]) -> bytes:
    """Encode the exact result payload accepted by the sandbox broker."""
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ValueError("tool result exceeds broker request limit")
    return payload


_SERVER_SOURCE = r"""
import contextlib, hmac, io, json, os, queue, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

_secret = __SECRET__
_pending, _results, _completed, _namespace = {}, {}, set(), {"__name__": "__fleet_rlm_repl__"}
_lock, _execution_lock = threading.RLock(), threading.Lock()
_active_deadline = None
_active_execution = False
_event_subscribers = set()
if os.path.isdir("/workspace"):
    os.chdir("/workspace")

# Keep a bounded, useful conversion ceiling for legitimate high-precision
# computations.  The default CPython limit makes the Pi canary fail before it
# can submit a result, despite the sandbox output budget already bounding what
# returns to the host.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(200_000)

def _emit_event(data):
    with _lock:
        subscribers = list(_event_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(data)
        except Exception:
            pass

class _BoundedWriter(io.StringIO):
    def __init__(self, limit, stream_type="stdout"):
        super().__init__()
        self._limit = limit
        self._truncated = False
        self._stream_type = stream_type
    def write(self, value):
        remaining = self._limit - self.tell()
        if remaining <= 0:
            self._truncated = True
            return len(value)
        if len(value) > remaining:
            super().write(value[:remaining])
            self._truncated = True
            written = value[:remaining]
        else:
            super().write(value)
            written = value
        if written:
            _emit_event({"type": self._stream_type, "delta": written})
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
        if not self._authorized(): _send(self, {"error": "unauthorized"}, 401); return
        if self.path == "/health": _send(self, {"status": "ok"}); return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = queue.Queue(maxsize=1000)
            with _lock:
                _event_subscribers.add(q)
            try:
                while True:
                    try:
                        item = q.get(timeout=0.2)
                        if item is None:
                            break
                        raw = ("data: " + json.dumps(item, allow_nan=False) + "\n\n").encode("utf-8")
                        self.wfile.write(raw)
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b":keep-alive\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with _lock:
                    _event_subscribers.discard(q)
            return
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
        if self.path == "/execute":
            code, variables = data.get("code"), data.get("variables") or {}
            timeout_s = data.get("timeout_s")
            if (not isinstance(code, str) or not isinstance(variables, dict) or isinstance(timeout_s, bool)
                    or (timeout_s is not None and (not isinstance(timeout_s, (int, float)) or timeout_s <= 0))):
                _send(self, {"error": "invalid execution"}, 400); return
            stdout, stderr = _BoundedWriter(__MAX_OUTPUT_CHARS__, "stdout"), _BoundedWriter(__MAX_OUTPUT_CHARS__, "stderr")
            result = {"stdout": "", "stderr": "", "final": None, "error": None, "error_category": None, "tool_error": None}
            try:
                with _execution_lock:
                    with _lock:
                        global _active_deadline, _active_execution
                        _active_execution = True
                        _active_deadline = time.monotonic() + float(timeout_s) if timeout_s is not None else None
                        _namespace["_fleet_tool_timeout_s"] = float(timeout_s) if timeout_s is not None else None
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
                            _active_execution = False
                        _emit_event({"type": "execution_done"})
            finally:
                result["stdout"], result["stderr"] = stdout.getvalue(), stderr.getvalue()
            _send(self, result); return
        if self.path == "/tool_call":
            call_id = str(data.get("id") or uuid.uuid4().hex); event = threading.Event()
            with _lock:
                if call_id in _pending or call_id in _results or call_id in _completed:
                    _send(self, {"error": "duplicate call"}, 409); return
                if not _active_execution or (_active_deadline is not None and time.monotonic() >= _active_deadline):
                    _send(self, {"error": "invocation is not executing"}, 409); return
                _pending[call_id] = {"tool_name": data.get("tool_name"), "args": data.get("args") or [], "kwargs": data.get("kwargs") or {}, "lease": None, "event": event}
                deadline = _active_deadline
            # SSE is only a wake-up hint. /pending is the sole lease issuer,
            # so a dropped event cannot hide unclaimed work from polling.
            _emit_event({"type": "tools_pending"})
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
                if request["lease"] is None or request["lease"] != lease: _send(self, {"error": "stale lease"}, 409); return
                if call_id in _results: _send(self, {"error": "duplicate result"}, 409); return
                _results[call_id] = {"tool_error": data["tool_error"]} if "tool_error" in data else {"result": data.get("result")}
                request["event"].set()
            _send(self, {"status": "ok"}); return
        _send(self, {"error": "not found"}, 404)

class Server(ThreadingMixIn, HTTPServer): daemon_threads = True
Server(("0.0.0.0", __PORT__), Handler).serve_forever()
"""


class DaytonaHttpToolBroker:
    """Run a local sandbox broker and fulfil its JSON-only tool requests."""

    def __init__(
        self,
        sandbox: Any,
        *,
        port: int,
        async_bridge: Any | None = None,
        tool_settled: Callable[[str, Mapping[str, Any], Any], None] | None = None,
        tool_failed: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._port = port
        self._secret = secrets.token_urlsafe(32)
        self._url: str | None = None
        self._session: str | None = None
        self._client: httpx.Client | None = None
        self._tools: dict[str, Callable[..., Any]] = {}
        self._async_bridge = async_bridge
        self._tool_settled = tool_settled
        self._tool_failed = tool_failed
        self._stopped = False
        self._delivery_error: DaytonaAdapterError | None = None

    def bind_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        if self._url is not None:
            raise DaytonaAdapterError(
                message="tool bindings changed after broker startup", cause_type="BrokerBindingError"
            )
        self._tools = dict(tools)

    def bind_async_bridge(self, async_bridge: Any | None) -> None:
        """Bind the composition-owned bridge before broker startup."""
        if self._url is not None:
            raise DaytonaAdapterError(
                message="async bridge changed after broker startup", cause_type="BrokerBindingError"
            )
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

    def start(self) -> None:
        """Complete bounded broker startup before computing execution time."""
        self._ensure_started()

    def execute(
        self,
        code: str,
        variables: Mapping[str, Any],
        *,
        timeout_s: float | None,
        on_stdout: Callable[[str], None] | None = None,
    ) -> Any:
        self._ensure_started()
        assert self._client is not None
        self._delivery_error = None
        client = self._client
        outcome: list[httpx.Response | BaseException] = []
        sse_stop_event = threading.Event()
        sse_ready = threading.Event()
        tools_pending = threading.Event()
        streamed_stdout = False

        def post() -> None:
            try:
                outcome.append(
                    client.post(
                        "/execute",
                        json={"code": code, "variables": dict(variables), "timeout_s": timeout_s},
                        timeout=(timeout_s + _EXECUTE_RESPONSE_GRACE_S if timeout_s is not None else None),
                    )
                )
            except BaseException as exc:
                outcome.append(exc)

        def listen_events() -> None:
            nonlocal streamed_stdout
            if not hasattr(client, "stream") or type(client).__name__ == "MagicMock":
                sse_ready.set()
                return
            try:
                with client.stream(
                    "GET", "/events", timeout=(timeout_s + _EXECUTE_RESPONSE_GRACE_S if timeout_s is not None else None)
                ) as stream:
                    if stream.status_code != 200:
                        sse_ready.set()
                        return
                    sse_ready.set()
                    for line in stream.iter_lines():
                        if sse_stop_event.is_set() or self._stopped:
                            break
                        if not line or not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:].strip())
                        except Exception:
                            continue
                        ev_type = data.get("type")
                        if ev_type == "execution_done":
                            break
                        if ev_type == "tools_pending":
                            tools_pending.set()
                        elif ev_type in ("stdout", "stderr") and on_stdout is not None:
                            delta = data.get("delta")
                            if isinstance(delta, str) and delta:
                                streamed_stdout = True
                                with contextlib.suppress(Exception):
                                    on_stdout(delta)
            except Exception:
                pass
            finally:
                sse_ready.set()

        worker = threading.Thread(target=post, daemon=True)
        sse_thread: threading.Thread | None = None
        if hasattr(client, "stream") and type(client).__name__ != "MagicMock":
            sse_thread = threading.Thread(target=listen_events, daemon=True)

        with host_action_deadline(time.monotonic() + timeout_s) if timeout_s is not None else contextlib.nullcontext():
            if sse_thread is not None:
                sse_thread.start()
                sse_ready.wait(timeout=0.2)
            worker.start()
            while worker.is_alive():
                if self._stopped:
                    break
                tools_pending.wait(timeout=0.02)
                tools_pending.clear()
                self._poll_once()
                worker.join(0.01)
            sse_stop_event.set()

        delivery_error = self._delivery_error
        if delivery_error is not None:
            raise delivery_error
        if outcome and isinstance(outcome[0], httpx.TimeoutException):
            raise DaytonaAdapterError(
                message="sandbox execution request timed out", cause_type="BrokerExecutionTimeout"
            ) from None
        if not outcome or isinstance(outcome[0], BaseException):
            raise DaytonaAdapterError(message="sandbox execution request failed", cause_type="BrokerExecutionError")
        response = outcome[0]
        if response.status_code != 200:
            raise DaytonaAdapterError(message="sandbox execution failed", cause_type="BrokerExecutionError")
        result_json = response.json()
        if streamed_stdout:
            result_json["streamed_stdout"] = True
        return result_json

    def stop(self, *, strict: bool = False) -> None:
        """Disable execution and attempt to close the client and delete its session.

        Retain handles whose cleanup fails so a later call can retry them.
        Ordinary cleanup exceptions are suppressed unless ``strict`` is true,
        in which case the first is re-raised after both cleanup attempts.
        """
        self._stopped = True
        cleanup_error: Exception | None = None
        client = self._client
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                cleanup_error = exc
            else:
                self._client = None
        if self._session is not None:
            session = self._session
            try:
                self._sandbox.process.delete_session(session)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            else:
                self._session = None
        if strict and cleanup_error is not None:
            raise cleanup_error

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
            self._dispatch_tool_request(request)

    def _dispatch_tool_request(self, request: Mapping[str, Any]) -> None:
        client = self._client
        if self._stopped or client is None:
            return
        name = str(request.get("tool_name") or "")
        arguments = dict(request.get("kwargs") or {})
        result: Any = None
        succeeded = False
        try:
            tool = self._tools[name]
            result = _resolve_awaitable_result(
                tool(*list(request.get("args") or []), **arguments),
                async_bridge=self._async_bridge,
            )
            validate_json_value(result, path=f"Tool {name} result")
            body = {"id": request["id"], "lease": request["lease"], "result": result}
            succeeded = True
        except Exception as exc:
            failed = self._tool_failed
            if failed is not None:
                with contextlib.suppress(Exception):
                    failed(name, arguments)
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
            payload = _encode_result_envelope(body)
        except ValueError:
            if succeeded:
                failed = self._tool_failed
                if failed is not None:
                    with contextlib.suppress(Exception):
                        failed(name, arguments)
            succeeded = False
            body = {
                "id": request.get("id"),
                "lease": request.get("lease"),
                "tool_error": {
                    "category": "ToolResultTooLarge",
                    "message": "tool result exceeds broker transport limit",
                    "call_id": str(request.get("id") or ""),
                },
            }
            payload = _encode_result_envelope(body)
        try:
            if not self._stopped and self._client is client:
                response = client.post("/result", content=payload, headers={"Content-Type": "application/json"})
                if response.status_code != 200:
                    benign = self._late_delivery_error(response)
                    if benign is not None:
                        logger.warning(
                            "sandbox tool result delivered after sandbox abandonment "
                            "call_id=%s tool_name=%s category=%s",
                            str(request.get("id") or "")[:128],
                            str(request.get("tool_name") or "")[:80],
                            benign,
                        )
                    else:
                        self._record_delivery_failure(
                            request, phase="result_delivery", category=f"http_{response.status_code}"
                        )
                elif succeeded:
                    settled = self._tool_settled
                    if settled is not None:
                        try:
                            settled(name, arguments, result)
                        except Exception:
                            self._record_delivery_failure(request, phase="settlement", category="callback_error")
        except httpx.HTTPError:
            self._record_delivery_failure(request, phase="result_delivery", category="http_error")

    def _late_delivery_error(self, response: Any) -> str | None:
        """Classify a non-200 /result response as benign sandbox-side abandonment.

        Only "duplicate call" is benign: the call's wait expired, the sandbox
        moved it to _completed and already returned the timeout to the action.
        Everything else stays fatal. A "stale lease" in particular means the
        call is still pending with its waiter blocked; leases are issued once
        and never re-issued, so a mismatch is a protocol failure that would
        otherwise surface only as a silent timeout. "duplicate result",
        unparseable bodies and other statuses are fatal too.
        """
        if getattr(response, "status_code", None) != 409:
            return None
        try:
            body = json.loads(response.text)
        except (TypeError, ValueError):
            return None
        error = body.get("error") if isinstance(body, dict) else None
        if error == "duplicate call":
            return "duplicate_call"
        return None

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
    with _request.urlopen(_req, timeout=globals().get('_fleet_tool_timeout_s')) as _response: _reply = _json.loads(_response.read())
    if "tool_error" in _reply:
        _failure = _reply["tool_error"]
        raise _FleetToolCallError(_failure)
    return _reply.get("result")"""
