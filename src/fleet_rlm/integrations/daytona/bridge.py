"""Minimal Daytona host-callback bridge for ``dspy.RLM`` interpreters.

Includes embedded broker assets (server code, tool wrapper templates) that
were previously in bridge_assets.py.

Uses the synchronous Daytona SDK directly — no async compatibility layer.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import keyword
import logging
import secrets
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Any, Callable

from dspy.primitives import CodeInterpreterError

from .async_compat import _run_sync_in_thread
from .errors import DaytonaDiagnosticError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedded broker assets
# ---------------------------------------------------------------------------

_BROKER_PORT = 3000
_BROKER_SERVER_PATH = "/home/daytona/broker_server.py"
_BROKER_SESSION_COMMAND = f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"
_BROKER_SERVER_CODE = """
\"\"\"Broker server for mediating tool calls between sandbox code and the host.

Uses only Python stdlib — no third-party dependencies required.
\"\"\"

import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_pending_requests: dict = {}
_results: dict = {}
_RESULT_TTL_S = 300.0  # orphan results older than this are swept
_BROKER_SECRET = __BROKER_SECRET__


def _sweep_stale_results(now):
    \"\"\"Remove orphan results nobody will collect (host crashed mid-post).\"\"\"
    stale = [cid for cid, ts in _results.items() if isinstance(ts, tuple) and now - ts[1] > _RESULT_TTL_S]
    for cid in stale:
        _results.pop(cid, None)


def _read_json(handler):
    length = int(handler.headers.get("Content-Length", 0))
    return json.loads(handler.rfile.read(length).decode("utf-8")) if length else {}


def _send_json(handler, data, status=200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _BrokerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress default access log

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            _send_json(self, {"status": "ok"})

        elif path == "/pending":
            qs = parse_qs(parsed.query)
            try:
                max_items = int(qs.get("max", ["1"])[0])
            except ValueError:
                max_items = 1
            max_items = max(1, max_items)
            try:
                lease_seconds = float(qs.get("lease_seconds", ["60"])[0])
            except ValueError:
                lease_seconds = 60.0
            lease_seconds = max(1.0, lease_seconds)

            requests_out = []
            with _lock:
                now = time.time()
                _sweep_stale_results(now)
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
                    requests_out.append({
                        "id": call_id,
                        "tool_name": payload["tool_name"],
                        "args": payload["args"],
                        "kwargs": payload["kwargs"],
                        "claim_token": claim_token,
                    })
            _send_json(self, {"requests": requests_out})

        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/tool_call" or path.startswith("/result/"):
            header_secret = self.headers.get("X-Broker-Secret", "")
            if not hmac.compare_digest(header_secret, _BROKER_SECRET):
                _send_json(self, {"error": "unauthorized"}, 401)
                return

        if path == "/tool_call":
            data = _read_json(self)
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

            timeout = __DAYTONA_TOOL_CALL_TIMEOUT_S__
            interval = 0.05
            elapsed = 0.0
            while elapsed < timeout:
                with _lock:
                    if call_id in _results:
                        entry = _results.pop(call_id)
                        result = entry[0] if isinstance(entry, tuple) else entry
                        _pending_requests.pop(call_id, None)
                        _send_json(self, {"result": result})
                        return
                time.sleep(interval)
                elapsed += interval

            with _lock:
                _pending_requests.pop(call_id, None)
            _send_json(self, {"error": "Tool call timeout"}, 504)

        elif path.startswith("/result/"):
            call_id = path[len("/result/"):]
            data = _read_json(self)
            result = data.get("result")
            claim_token = str(data.get("claim_token") or "")
            with _lock:
                req = _pending_requests.get(call_id)
                if req is None:
                    _send_json(self, {"error": "Unknown or expired call_id"}, 404)
                    return
                expected_token = req.get("lease_token")
                if not expected_token or claim_token != expected_token:
                    _send_json(self, {"error": "Stale or invalid claim token"}, 409)
                    return
                _results[call_id] = (result, time.time())
                req["lease_token"] = None
            _send_json(self, {"status": "ok"})

        else:
            _send_json(self, {"error": "not found"}, 404)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    server = _ThreadedHTTPServer(("0.0.0.0", 3000), _BrokerHandler)
    server.serve_forever()
""".strip()

# Default broker tool-call polling timeout (used as fallback when no instance
# value is provided).  The placeholder ``__DAYTONA_TOOL_CALL_TIMEOUT_S__``
# in _BROKER_SERVER_CODE is replaced at instance level in ensure_started().
_DAYTONA_BROKER_TOOL_CALL_TIMEOUT_DEFAULT: float = 180.0

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
        headers={{"Content-Type": "application/json", "X-Broker-Secret": "{broker_secret}"}},
        method="POST",
    )
    with _urllib_request.urlopen(req, timeout=130) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"Tool call failed: {{data['error']}}")
    result = data.get("result")
    if isinstance(result, str):
        stripped_result = result.lstrip()
        if not stripped_result.startswith(("{{", "[")):
            return result
        try:
            return _json.loads(result)
        except (ValueError, _json.JSONDecodeError):
            return result
    return result
""".strip()


def _default_survives_repr(default: Any) -> bool:
    """Return True if the default's repr can be round-tripped through ``ast.literal_eval``.

    Non-repr-safe defaults (e.g. ``object()``) cannot be embedded in generated
    wrapper source code and must be converted to keyword-only parameters.
    """
    try:
        restored = ast.literal_eval(repr(default))
        # Use structural equality; NaN etc. will fail here (desired).
        return restored == default
    except (ValueError, SyntaxError, TypeError):
        return False


def generate_tool_wrapper(
    *,
    tool_name: str,
    tool_func: Callable[..., Any],
    broker_secret: str = "",
) -> str:
    """Generate a Python function wrapper that calls the host broker via HTTP."""
    signature = inspect.signature(tool_func)
    params = list(signature.parameters.values())
    sig_parts: list[str] = []
    args_list: list[str] = []
    kwargs_parts: list[str] = []
    added_kw_only_separator = False
    last_positional_only_idx = max(
        (index for index, param in enumerate(params) if param.kind == inspect.Parameter.POSITIONAL_ONLY),
        default=-1,
    )

    def _format_param(param: inspect.Parameter) -> str:
        if param.default is inspect.Parameter.empty:
            return param.name
        if not _default_survives_repr(param.default):
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
            if param.default is inspect.Parameter.empty:
                sig_parts.append(param.name)
                args_list.append(param.name)
            elif _default_survives_repr(param.default):
                sig_parts.append(_format_param(param))
                kwargs_parts.append(f'"{param.name}": {param.name}')
            else:
                # Non-repr-safe default → keyword-only, no default in signature
                if not added_kw_only_separator:
                    sig_parts.append("*")
                    added_kw_only_separator = True
                sig_parts.append(param.name)
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
        raise CodeInterpreterError(f"Unsupported parameter kind for tool '{tool_name}': {param.kind}")

    return _TOOL_WRAPPER_TEMPLATE.format(
        tool_name=tool_name,
        signature=", ".join(sig_parts),
        args_list=", ".join(args_list),
        kwargs_dict=", ".join(kwargs_parts),
        broker_port=_BROKER_PORT,
        broker_secret=broker_secret,
    )


# ---------------------------------------------------------------------------
# Broker circuit breaker (C3 reliability slice)
# ---------------------------------------------------------------------------


class BrokerCircuitBreaker:
    """Trip after N consecutive broker failures to avoid infinite retry.

    The host-side polling loop retries broker fetch failures with a short
    sleep and no upper bound, so a dead broker (e.g. sandbox killed out of
    band) pins a thread forever. The circuit breaker trips ``open`` after
    ``threshold`` consecutive failures, raises on the next check, and goes
    ``half_open`` after ``cooldown_seconds`` to allow a single probe.
    """

    __slots__ = ("_failures", "_opened_at", "_state", "cooldown_seconds", "threshold")

    def __init__(self, *, threshold: int = 10, cooldown_seconds: float = 30.0) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self.threshold = int(threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self._failures = 0
        self._opened_at: float | None = None
        self._state = "closed"  # closed | open | half_open

    @property
    def state(self) -> str:
        return self._state

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._state = "open"
            self._opened_at = time.monotonic()

    def raise_if_open(self) -> None:
        """Raise CodeInterpreterError if the circuit is open past cooldown."""
        if self._state != "open":
            return
        assert self._opened_at is not None
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # Half-open: allow one probe call through.
            self._state = "half_open"
            return
        raise CodeInterpreterError(
            f"Broker circuit breaker tripped after {self._failures} consecutive failures "
            f"(cooldown {self.cooldown_seconds}s)"
        )


# ---------------------------------------------------------------------------
# Bridge runtime
# ---------------------------------------------------------------------------


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
        broker_health_timeout: float = 60.0,
        broker_tool_call_timeout: float = 180.0,
        broker_start_retries: int = 1,
    ) -> None:
        if max_concurrent_tool_calls < 1:
            raise ValueError("max_concurrent_tool_calls must be >= 1")
        if tool_claim_lease_seconds < 1:
            raise ValueError("tool_claim_lease_seconds must be >= 1")
        if broker_health_timeout < 1:
            raise ValueError("broker_health_timeout must be >= 1")
        if broker_tool_call_timeout < 1:
            raise ValueError("broker_tool_call_timeout must be >= 1")
        if broker_start_retries < 0:
            raise ValueError("broker_start_retries must be >= 0")
        self.sandbox = sandbox
        self.context = context
        self.max_concurrent_tool_calls = max_concurrent_tool_calls
        self.tool_claim_lease_seconds = float(tool_claim_lease_seconds)
        self.broker_health_timeout = float(broker_health_timeout)
        self.broker_tool_call_timeout = float(broker_tool_call_timeout)
        self.broker_start_retries = int(broker_start_retries)
        self._broker_secret: str | None = None
        self._broker_url: str | None = None
        self._broker_token: str | None = None
        self._broker_session_id: str | None = None
        self._injected_tools: set[str] = set()
        self._tool_executor_pool: ThreadPoolExecutor | None = None
        self._tool_executor_lock = Lock()
        self._broker_circuit = BrokerCircuitBreaker(threshold=10, cooldown_seconds=30.0)

    def bind_context(self, context: Any) -> None:
        self.context = context

    def ensure_started(self) -> None:
        if self._broker_url is not None:
            return
        if self._broker_secret is None:
            self._broker_secret = secrets.token_urlsafe(32)
        server_code = _BROKER_SERVER_CODE.replace(
            "__DAYTONA_TOOL_CALL_TIMEOUT_S__",
            repr(self.broker_tool_call_timeout),
        ).replace("__BROKER_SECRET__", repr(self._broker_secret))
        self.sandbox.fs.upload_file(
            server_code.encode("utf-8"),
            _BROKER_SERVER_PATH,
        )
        # sha256 integrity check — verify the uploaded file matches what we sent.
        expected_sha = hashlib.sha256(server_code.encode("utf-8")).hexdigest()
        verify_cmd = (
            f'python -c "'
            f"import hashlib; "
            f"print(hashlib.sha256(open('{_BROKER_SERVER_PATH}','rb').read()).hexdigest())"
            f'"'
        )
        verify_resp = self.sandbox.process.exec(verify_cmd)
        actual_sha = (verify_resp.result or "").strip()
        if actual_sha != expected_sha:
            raise DaytonaDiagnosticError(
                f"Broker asset integrity check failed: expected={expected_sha}, actual={actual_sha}",
                category="broker_asset_integrity",
                phase="broker_start",
            )
        from daytona import SessionExecuteRequest

        last_error: Exception | None = None
        for attempt in range(self.broker_start_retries + 1):
            session_id = f"broker-{uuid.uuid4().hex[:8]}"
            try:
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
                self._wait_health(timeout=self.broker_health_timeout)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Broker start attempt %d/%d failed: %s",
                    attempt + 1,
                    self.broker_start_retries + 1,
                    exc,
                )
                # Clean up the failed session attempt and reset state so
                # the next attempt (or the next ensure_started call) starts
                # from scratch instead of caching a broken broker.
                self._broker_url = None
                self._broker_token = None
                self._broker_session_id = None
                try:
                    self.sandbox.process.delete_session(session_id)
                except Exception:
                    pass  # Best-effort session cleanup
                if attempt < self.broker_start_retries:
                    time.sleep(0.5 * (attempt + 1))
                continue
        raise CodeInterpreterError(
            f"Broker server failed to start within timeout "
            f"({self.broker_health_timeout}s after {self.broker_start_retries + 1} attempt(s)): "
            f"{last_error}"
        )

    async def aensure_started(self) -> None:
        await _run_sync_in_thread(self.ensure_started)

    def register_tools(self, tools: dict[str, Callable[..., Any]]) -> None:
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
                raise CodeInterpreterError(f"Failed to inject tool '{tool_name}': {result.error.value}")
            self._injected_tools.add(tool_name)

    async def async_tools(self, tools: dict[str, Callable[..., Any]]) -> None:
        await _run_sync_in_thread(self.register_tools, tools)

    def execute_tool_call(
        self,
        *,
        code: str,
        timeout: int,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> DaytonaBridgeExecution:
        self.ensure_started()
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

        # Run code execution in a background thread so the main thread can
        # poll for tool callbacks concurrently.
        execution_result: list[Any] = []
        execution_error: list[Exception] = []
        execution_done = False

        def _run_code() -> None:
            nonlocal execution_done
            try:
                result = self.sandbox.code_interpreter.run_code(
                    code,
                    context=self.context,
                    on_stdout=_handle_stdout,
                    on_stderr=_handle_stderr,
                    timeout=timeout,
                )
                execution_result.append(result)
            except Exception as exc:
                execution_error.append(exc)
            finally:
                execution_done = True

        code_thread = Thread(target=_run_code, daemon=True)
        code_thread.start()
        join_timeout = self._code_thread_join_timeout(timeout)

        try:
            callback_count = self._poll_and_execute_tools(
                is_done=lambda: execution_done,
                tool_executor=tool_executor,
            )
        except Exception:
            code_thread.join(timeout=join_timeout)
            raise

        code_thread.join(timeout=join_timeout)
        if code_thread.is_alive():
            raise CodeInterpreterError("Bridge code execution thread did not finish after callback polling completed")

        if execution_error:
            raise execution_error[0]

        return DaytonaBridgeExecution(
            result=execution_result[0] if execution_result else None,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            callback_count=callback_count,
        )

    async def aexecute(
        self,
        *,
        code: str,
        timeout: int,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> DaytonaBridgeExecution:
        return await _run_sync_in_thread(
            self.execute_tool_call,
            code=code,
            timeout=timeout,
            tool_executor=tool_executor,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )

    def close(self) -> None:
        session_id = self._broker_session_id
        self._shutdown_tool_executor_pool()
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

    async def aclose(self) -> None:
        await _run_sync_in_thread(self.close)

    def _wait_health(self, timeout: float = 30.0) -> None:
        broker_url = self._broker_url
        if broker_url is None:
            raise CodeInterpreterError("Broker URL was not initialized.")
        started = time.time()
        while time.time() - started < timeout:
            try:
                if self._check_health(broker_url):
                    return
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                time.sleep(0.1)
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
        return generate_tool_wrapper(
            tool_name=tool_name,
            tool_func=tool_func,
            broker_secret=self._broker_secret or "",
        )

    def _code_thread_join_timeout(self, timeout: int) -> float:
        return min(max(float(timeout), 1.0), 5.0)

    def _get_tool_executor_pool(self) -> ThreadPoolExecutor:
        with self._tool_executor_lock:
            if self._tool_executor_pool is None:
                self._tool_executor_pool = ThreadPoolExecutor(
                    max_workers=self.max_concurrent_tool_calls,
                    thread_name_prefix="daytona-tool-bridge",
                )
            return self._tool_executor_pool

    def _shutdown_tool_executor_pool(self) -> None:
        with self._tool_executor_lock:
            pool = self._tool_executor_pool
            self._tool_executor_pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _poll_and_execute_tools(
        self,
        *,
        is_done: Callable[[], bool],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> int:
        broker_url = self._broker_url
        if broker_url is None:
            return 0

        callback_count = 0

        def _fetch_pending(max_items: int) -> list[dict[str, Any]]:
            request = urllib.request.Request(
                (f"{broker_url}/pending?max={max_items}&lease_seconds={self.tool_claim_lease_seconds}"),
                headers=self._preview_headers(),
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("requests"), list):
                return [item for item in payload["requests"] if isinstance(item, dict) and item.get("id")]
            return []

        def _post_result(call_id: str, result: Any, claim_token: str | None) -> None:
            encoded_result = result if isinstance(result, str) else json.dumps(result)
            payload = json.dumps({"result": encoded_result, "claim_token": claim_token}).encode("utf-8")
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
            safe_kwargs = {str(key): value for key, value in kwargs.items()} if isinstance(kwargs, dict) else {}
            try:
                result = tool_executor(tool_name, safe_args, safe_kwargs)
            except Exception as exc:  # pragma: no cover - host callback boundary
                result = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                _post_result(call_id, result, claim_token)
            except urllib.error.HTTPError as exc:
                if exc.code not in {404, 409}:
                    raise

        pool = self._get_tool_executor_pool()
        inflight: dict[str, Any] = {}  # call_id -> Future

        while not is_done() or inflight:
            # Reap completed futures
            done_ids = [cid for cid, fut in inflight.items() if fut.done()]
            for cid in done_ids:
                fut = inflight.pop(cid)
                # Propagate any unexpected exceptions from the future
                exc = fut.exception()
                if exc is not None:
                    raise exc
                callback_count += 1

            if is_done() and not inflight:
                break

            if is_done():
                # Code is done but we still have inflight tool calls — just wait
                time.sleep(0.005)
                continue

            capacity = self.max_concurrent_tool_calls - len(inflight)
            if capacity <= 0:
                time.sleep(0.01)
                continue

            try:
                pending_items = _fetch_pending(capacity)
                self._broker_circuit.record_success()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                self._broker_circuit.record_failure()
                self._broker_circuit.raise_if_open()
                time.sleep(0.01)
                continue

            for pending in pending_items:
                call_id = str(pending.get("id") or "")
                if not call_id or call_id in inflight:
                    continue
                inflight[call_id] = pool.submit(_execute_one, pending)

            time.sleep(0.01)

        return callback_count


__all__ = ["DaytonaBridgeExecution", "DaytonaToolBridge"]
