"""HTTP-in-sandbox host-tool broker for Daytona RLM mediation.

Transport choice for B1: Daytona-appropriate HTTP broker inside the sandbox
with host-side poll (mirrors legacy Fleet bridge semantics; not Deno JSON-RPC).
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import keyword
import re
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Thread
from typing import TYPE_CHECKING, Any

import httpx
from daytona import SessionExecuteRequest

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    map_provider_error,
    provider_status_code,
    sanitize_provider_message,
)

if TYPE_CHECKING:
    from fleet_rlm.daytona.interpreter import BackendExecutionResult

DEFAULT_BROKER_PORT = 3000
_PREVIEW_LINK_RETRY_DELAYS = (0.25, 0.5)
_BROKER_SERVER_PATH = "/home/daytona/fleet_rlm_broker_server.py"
_BROKER_SESSION_COMMAND = f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"
_FINAL_OUTPUT_MARKER = "__FLEET_FINAL_OUTPUT__"


def _is_retryable_preview_link_error(exc: DaytonaAdapterError) -> bool:
    """Retry only transient provider failures while resolving the preview URL."""
    if is_safe_pre_creation_retry(exc):
        return True
    return re.search(r"\b5\d{2}\b", sanitize_provider_message(str(exc))) is not None


class FleetFinalOutputError(Exception):
    """Raised inside an interpreter when SUBMIT completes successfully."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        super().__init__("Final output submitted")


def build_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    return _typed_submit_source(output_fields) if output_fields else _generic_submit_source()


def remote_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    return f"""
import json as _json
_FINAL_OUTPUT_MARKER = {_FINAL_OUTPUT_MARKER!r}

class FleetFinalOutputError(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__("Final output submitted")

{build_submit_setup_code(output_fields)}
""".strip()


def _generic_submit_source() -> str:
    return """
def SUBMIT(**kwargs):
    print(f"{_FINAL_OUTPUT_MARKER}{_json.dumps(kwargs, ensure_ascii=False)}{_FINAL_OUTPUT_MARKER}")
    raise FleetFinalOutputError(kwargs)
""".strip()


def _typed_submit_source(output_fields: list[dict[str, Any]]) -> str:
    signature_parts: list[str] = []
    result_parts: list[str] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        type_hint = str(field.get("type") or "").strip()
        signature_parts.append(f"{name}: {type_hint}" if type_hint else name)
        result_parts.append(f'"{name}": {name}')
    signature = ", ".join(signature_parts) or "**kwargs"
    body = f"result = {{{', '.join(result_parts)}}}" if result_parts else "result = dict(kwargs)"
    return f"""
def SUBMIT({signature}):
    {body}
    print(f"{{_FINAL_OUTPUT_MARKER}}{{_json.dumps(result, ensure_ascii=False)}}{{_FINAL_OUTPUT_MARKER}}")
    raise FleetFinalOutputError(result)
""".strip()


def extract_final_payload(stdout: str, *, marker: str = _FINAL_OUTPUT_MARKER) -> dict[str, Any] | None:
    start = stdout.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = stdout.find(marker, start)
    if end == -1:
        return None
    try:
        parsed = json.loads(stdout[start:end])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_BROKER_SERVER_CODE = """
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_pending_requests = {}
_results = {}
_BROKER_SECRET = __BROKER_SECRET__


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
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _send_json(self, {"status": "ok"})
            return
        if parsed.path == "/pending":
            qs = parse_qs(parsed.query)
            try:
                max_items = max(1, int(qs.get("max", ["1"])[0]))
            except ValueError:
                max_items = 1
            out = []
            with _lock:
                for call_id, req in list(_pending_requests.items()):
                    if req.get("lease_token"):
                        continue
                    token = uuid.uuid4().hex
                    req["lease_token"] = token
                    out.append({
                        "id": call_id,
                        "tool_name": req.get("tool_name"),
                        "args": req.get("args") or [],
                        "kwargs": req.get("kwargs") or {},
                        "lease_token": token,
                    })
                    if len(out) >= max_items:
                        break
            _send_json(self, {"requests": out})
            return
        _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        secret = self.headers.get("X-Broker-Secret", "")
        if not hmac.compare_digest(secret, _BROKER_SECRET):
            _send_json(self, {"error": "unauthorized"}, 401)
            return
        data = _read_json(self)
        if parsed.path == "/tool_call":
            call_id = str(data.get("id") or uuid.uuid4())
            event = threading.Event()
            with _lock:
                _pending_requests[call_id] = {
                    "tool_name": data.get("tool_name"),
                    "args": data.get("args") or [],
                    "kwargs": data.get("kwargs") or {},
                    "lease_token": None,
                    "event": event,
                }
            if not event.wait(timeout=180.0):
                with _lock:
                    _pending_requests.pop(call_id, None)
                _send_json(self, {"error": "tool call timed out"}, 504)
                return
            with _lock:
                result = _results.pop(call_id, None)
                _pending_requests.pop(call_id, None)
            if result is None:
                _send_json(self, {"error": "missing result"}, 500)
                return
            if isinstance(result, dict) and "error" in result:
                _send_json(self, result, 500)
                return
            _send_json(self, {"result": result})
            return
        if parsed.path == "/result":
            call_id = str(data.get("id") or "")
            claim = data.get("lease_token")
            with _lock:
                req = _pending_requests.get(call_id)
                if req is None:
                    _send_json(self, {"error": "unknown call"}, 404)
                    return
                if claim != req.get("lease_token"):
                    _send_json(self, {"error": "stale claim"}, 409)
                    return
                if "error" in data:
                    _results[call_id] = {"error": data.get("error")}
                else:
                    _results[call_id] = data.get("result")
                req["lease_token"] = None
                event = req.get("event")
            if event is not None:
                event.set()
            _send_json(self, {"status": "ok"})
            return
        _send_json(self, {"error": "not found"}, 404)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    server = _ThreadedHTTPServer(("0.0.0.0", __BROKER_PORT__), _BrokerHandler)
    server.serve_forever()
""".strip()

_TOOL_WRAPPER_TEMPLATE = """
def {tool_name}({signature}):
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
    return data.get("result")
""".strip()


class DaytonaHttpToolBroker:
    """Start/register/poll host-tool mediation against a Daytona sandbox."""

    def __init__(
        self,
        *,
        sandbox: Any,
        broker_port: int = DEFAULT_BROKER_PORT,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._sandbox = sandbox
        if not isinstance(broker_port, int) or isinstance(broker_port, bool) or not 0 < broker_port <= 65_535:
            raise ValueError(f"broker_port must be between 1 and 65535, got {broker_port!r}")
        self._broker_port = broker_port
        self._poll_interval_s = poll_interval_s
        self._broker_secret = secrets.token_urlsafe(32)
        self._broker_url: str | None = None
        self._broker_token: str | None = None
        self._broker_session_id: str | None = None
        self._injected_tools: set[str] = set()
        self._pending_wrappers: list[str] = []
        self._stopped = False
        # One pooled client for the whole broker lifetime: the preview proxy
        # sits behind TLS, so per-request urllib connections paid a handshake
        # on every 50 ms poll tick. Stats are per execute_with_callbacks call.
        self._client: httpx.Client | None = None
        self._poll_count = 0
        self._fulfilled_count = 0
        self.last_execution_stats: dict[str, int] = {}

    def _http(self) -> httpx.Client:
        if self._client is None:
            # Every call site runs after ensure_started set the broker URL;
            # fail loudly rather than building a client with no base URL.
            assert self._broker_url is not None
            self._client = httpx.Client(
                base_url=self._broker_url,
                headers=self._preview_headers(),
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        return self._client

    def ensure_started(self) -> None:
        if self._broker_url is not None or self._stopped:
            return
        server_code = _BROKER_SERVER_CODE.replace("__BROKER_SECRET__", repr(self._broker_secret)).replace(
            "__BROKER_PORT__", str(self._broker_port)
        )
        self._sandbox.fs.upload_file(server_code.encode("utf-8"), _BROKER_SERVER_PATH)
        expected_sha = hashlib.sha256(server_code.encode("utf-8")).hexdigest()
        verify = self._sandbox.process.code_run(
            "import hashlib; print(hashlib.sha256("
            "open('/home/daytona/fleet_rlm_broker_server.py','rb').read()).hexdigest())"
        )
        actual_sha = str(getattr(verify, "result", "") or "").strip()
        if actual_sha != expected_sha:
            msg = "broker asset integrity check failed"
            raise DaytonaAdapterError(message=msg, cause_type="BrokerIntegrityError")

        session_id = f"fleet-clean-broker-{uuid.uuid4().hex[:8]}"
        self._sandbox.process.create_session(session_id)
        self._sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(command=_BROKER_SESSION_COMMAND, run_async=True),
        )
        # Retain the session before contacting the preview proxy so a failed
        # preview lookup can still be cleaned up by ``stop``.
        self._broker_session_id = session_id
        preview = self._get_preview_link_with_retry()
        self._broker_url = str(preview.url).rstrip("/")
        self._broker_token = str(getattr(preview, "token", "") or "")
        self._wait_health(timeout_s=60.0)

    def _get_preview_link_with_retry(self) -> Any:
        last_error: DaytonaAdapterError | None = None
        attempts = 0
        for retry_delay in (0.0, *_PREVIEW_LINK_RETRY_DELAYS):
            if retry_delay:
                time.sleep(retry_delay)
            attempts += 1
            try:
                return self._sandbox.get_preview_link(self._broker_port)
            except Exception as exc:
                mapped = map_provider_error(exc)
                last_error = mapped
                if not _is_retryable_preview_link_error(mapped):
                    break

        assert last_error is not None
        raise ProviderRequestError(
            message=sanitize_provider_message(
                f"Daytona preview link request for port {self._broker_port} failed after "
                f"{attempts} attempts: {last_error}"
            ),
            cause_type="PreviewLinkError",
            status_code=provider_status_code(last_error),
        ) from last_error

    def register_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self.ensure_started()
        for name, fn in tools.items():
            if name in self._injected_tools:
                continue
            if not name.isidentifier() or keyword.iskeyword(name):
                msg = f"invalid tool name: {name}"
                raise DaytonaAdapterError(message=msg, cause_type="InvalidToolNameError")
            self._pending_wrappers.append(self._tool_wrapper_source(name, fn))
            self._injected_tools.add(name)

    def drain_wrapper_sources(self) -> str:
        wrappers = self._pending_wrappers
        self._pending_wrappers = []
        return "\n\n".join(wrappers)

    def submit_setup_code(self, output_fields: list[dict[str, Any]] | None) -> str:
        wrappers = self.drain_wrapper_sources()
        submit = remote_submit_setup_code(output_fields)
        if wrappers:
            return f"{wrappers}\n\n{submit}"
        return submit

    def execute_with_callbacks(
        self,
        *,
        run_code: Callable[[], str | BackendExecutionResult],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> BackendExecutionResult:
        from fleet_rlm.daytona.interpreter import BackendExecutionResult

        self.ensure_started()
        if self._stopped:
            msg = "broker already stopped"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")

        bucket: list[str | BackendExecutionResult | BaseException] = []

        def _runner() -> None:
            try:
                bucket.append(run_code())
            except BaseException as exc:
                bucket.append(exc)

        poll_start = self._poll_count
        fulfilled_start = self._fulfilled_count
        thread = Thread(target=_runner, daemon=True)
        thread.start()
        while thread.is_alive():
            if self._stopped:
                break
            self._poll_once(tool_executor)
            time.sleep(self._poll_interval_s)
        thread.join(timeout=1.0)
        for _ in range(5):
            if not self._poll_once(tool_executor):
                break
            time.sleep(self._poll_interval_s)
        self.last_execution_stats = {
            "poll_count": self._poll_count - poll_start,
            "tool_call_count": self._fulfilled_count - fulfilled_start,
        }

        if not bucket:
            msg = "sandbox execution produced no result"
            raise DaytonaAdapterError(message=msg, cause_type="BrokerExecutionError")
        outcome = bucket[0]
        if isinstance(outcome, BaseException):
            if isinstance(outcome, DaytonaAdapterError):
                raise outcome
            raise DaytonaAdapterError(
                message=sanitize_provider_message(str(outcome)),
                cause_type=type(outcome).__name__,
            ) from outcome
        if isinstance(outcome, BackendExecutionResult):
            return outcome
        final = extract_final_payload(str(outcome))
        return BackendExecutionResult(stdout=str(outcome), final=final)

    def stop(self) -> None:
        self._stopped = True
        session_id = self._broker_session_id
        self._broker_session_id = None
        self._broker_url = None
        self._broker_token = None
        # Swap before closing so a concurrent late _http() caller never
        # observes the client mid-close; in-flight requests on the detached
        # client fail into the suppressed httpx error paths.
        client = self._client
        self._client = None
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
        if session_id is None:
            return
        with contextlib.suppress(Exception):
            self._sandbox.process.delete_session(session_id)

    def _wait_health(self, *, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        last_error = "unreachable"
        client = self._http()
        while time.monotonic() < deadline:
            try:
                resp = client.get("/health", timeout=5)
                if resp.status_code == 200:
                    return
                last_error = f"HTTP {resp.status_code}"
                # Preview-proxy auth failures are not transient; do not burn the
                # full health timeout retrying a credential/token rejection.
                if resp.status_code in {401, 403}:
                    detail = last_error
                    if not self._broker_token:
                        detail = f"{detail} (preview token missing)"
                    raise DaytonaAdapterError(
                        message=sanitize_provider_message(f"broker health check failed: {detail}"),
                        cause_type="BrokerHealthAuthError",
                    )
            except DaytonaAdapterError:
                raise
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise DaytonaAdapterError(
            message=sanitize_provider_message(f"broker health check failed: {last_error}"),
            cause_type="BrokerHealthError",
        )

    def _poll_once(self, tool_executor: Callable[[str, list[Any], dict[str, Any]], Any]) -> bool:
        """Poll for pending broker requests and fulfill them concurrently.

        Parameters:
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes each requested tool.

        Returns:
            bool: `true` if pending requests were found and processed, `false` otherwise.
        """
        assert self._broker_url is not None
        self._poll_count += 1
        try:
            payload = self._http().get("/pending", params={"max": "8"}, timeout=5).json()
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return False
        requests_out = payload.get("requests") or []
        if not requests_out:
            return False
        self._fulfilled_count += len(requests_out)
        # The broker polls on the interpreter thread, then fulfills host Tools
        # in worker threads. Copy the active Turn/MLflow context separately for
        # each request so Tool spans stay nested without sharing a Context.
        work = [(copy_context(), item) for item in requests_out]
        with ThreadPoolExecutor(max_workers=min(8, len(requests_out))) as pool:
            list(pool.map(lambda item: item[0].run(self._fulfill, item[1], tool_executor), work))
        return True

    def _fulfill(
        self,
        item: dict[str, Any],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> None:
        """
        Fulfill a pending tool request and submit its result or sanitized error to the broker.

        Parameters:
            item (dict[str, Any]): Pending tool request containing its identifier, lease token, tool name,
                arguments, and keyword arguments.
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes the requested tool.
        """
        call_id = str(item.get("id") or "")
        lease = item.get("lease_token")
        name = str(item.get("tool_name") or "")
        args = list(item.get("args") or [])
        kwargs = dict(item.get("kwargs") or {})
        try:
            result = tool_executor(name, args, kwargs)
            body: dict[str, Any] = {"id": call_id, "lease_token": lease, "result": result}
        except Exception as exc:
            body = {
                "id": call_id,
                "lease_token": lease,
                "error": sanitize_provider_message(str(exc)),
            }
        try:
            self._http().post(
                "/result",
                content=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        except (httpx.HTTPError, TimeoutError, OSError):
            return

    def _preview_headers(self) -> dict[str, str]:
        headers = {"X-Broker-Secret": self._broker_secret}
        if self._broker_token:
            headers["X-Daytona-Preview-Token"] = self._broker_token
        return headers

    def _tool_wrapper_source(self, tool_name: str, tool_func: Callable[..., Any]) -> str:
        signature = inspect.signature(tool_func)
        params = list(signature.parameters.values())
        sig_parts: list[str] = []
        args_list: list[str] = []
        kwargs_parts: list[str] = []
        for param in params:
            if param.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            name = param.name
            if param.default is inspect.Parameter.empty:
                sig_parts.append(name)
            else:
                sig_parts.append(f"{name}={param.default!r}")
            if param.kind == inspect.Parameter.KEYWORD_ONLY or (
                param.default is not inspect.Parameter.empty and param.kind != inspect.Parameter.POSITIONAL_ONLY
            ):
                kwargs_parts.append(f'"{name}": {name}')
            else:
                args_list.append(name)
        return _TOOL_WRAPPER_TEMPLATE.format(
            tool_name=tool_name,
            signature=", ".join(sig_parts),
            args_list=", ".join(args_list),
            kwargs_dict=", ".join(kwargs_parts),
            broker_port=self._broker_port,
            broker_secret=self._broker_secret,
        )
