"""HTTP-in-sandbox host-tool broker for Daytona RLM mediation.

Transport choice for B1: Daytona-appropriate HTTP broker inside the sandbox
with host-side poll (mirrors legacy Fleet bridge semantics; it is not a JSON-RPC broker).
"""

from __future__ import annotations

import base64
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
FINAL_OUTPUT_MARKER = "__FLEET_FINAL_OUTPUT__"
_FINAL_OUTPUT_MARKER = FINAL_OUTPUT_MARKER
_MAX_EXECUTE_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_EXECUTE_OUTPUT_CHARS = 64 * 1024


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
import base64 as _base64
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
import base64 as _base64

def SUBMIT(**kwargs):
    payload = _base64.b64encode(_json.dumps(kwargs, ensure_ascii=False).encode("utf-8")).decode("ascii")
    print(f"{_FINAL_OUTPUT_MARKER}{payload}{_FINAL_OUTPUT_MARKER}")
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
import base64 as _base64

def SUBMIT({signature}):
    {body}
    payload = _base64.b64encode(_json.dumps(result, ensure_ascii=False).encode("utf-8")).decode("ascii")
    print(f"{{_FINAL_OUTPUT_MARKER}}{{payload}}{{_FINAL_OUTPUT_MARKER}}")
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
    encoded = stdout[start:end]
    try:
        payload = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        # Accept the pre-envelope representation while older sandboxes drain.
        payload = encoded
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def final_output_frame(value: Mapping[str, Any], *, marker: str = _FINAL_OUTPUT_MARKER) -> str:
    """Return the exact private stdout frame emitted by ``SUBMIT``."""
    encoded = base64.b64encode(json.dumps(dict(value), ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f"{marker}{encoded}{marker}"


_BROKER_SERVER_CODE = """
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_pending_requests = {}
_results = {}
_execution_outputs = {}
_BROKER_SECRET = __BROKER_SECRET__
_MAX_REQUEST_BYTES = __MAX_REQUEST_BYTES__
_MAX_OUTPUT_CHARS = __MAX_OUTPUT_CHARS__
_CONTEXT_MOUNT_ROOT = __CONTEXT_MOUNT_ROOT__
_CONTEXT_MANIFEST_SHA256 = __CONTEXT_MANIFEST_SHA256__
_execution_lock = threading.Lock()
_context_accesses = []
_namespace = {"__name__": "__fleet_rlm_repl__"}


def _read_json(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length > _MAX_REQUEST_BYTES:
        raise ValueError("request is too large")
    return json.loads(handler.rfile.read(length).decode("utf-8")) if length else {}


def _send_json(handler, data, status=200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _decode_value(value):
    if isinstance(value, dict) and value.get("__fleet_type__") == "bytes":
        return base64.b64decode(str(value.get("data") or ""), validate=True)
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _decode_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("unsupported variable value")


def _fleet_load_context_manifest(raw_manifest):
    try:
        if isinstance(raw_manifest, str):
            raw_manifest = raw_manifest.encode("utf-8")
        if _CONTEXT_MOUNT_ROOT is None or _CONTEXT_MANIFEST_SHA256 is None:
            raise ValueError
        if hashlib.sha256(bytes(raw_manifest)).hexdigest() != _CONTEXT_MANIFEST_SHA256:
            raise ValueError
        manifest = json.loads(bytes(raw_manifest).decode("utf-8"))
        mount_root = os.path.realpath(str(_CONTEXT_MOUNT_ROOT))
        if os.path.realpath(str(manifest["mount_root"])) != mount_root:
            raise ValueError
        entries = list(manifest["entries"])
    except Exception as exc:
        raise ValueError("context manifest is invalid") from exc
    values = []
    for entry in entries:
        try:
            attachment_id = str(entry["attachment_id"])
            path = os.path.realpath(str(entry["sandbox_path"]))
            expected_size = int(entry["byte_size"])
            expected_sha = str(entry["checksum_sha256"])
            if os.path.commonpath((mount_root, path)) != mount_root or path == mount_root:
                raise ValueError
            with open(path, "rb") as handle:
                body = handle.read(expected_size + 1)
            if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError
            try:
                data = body.decode("utf-8")
                encoding = "utf-8"
                if any(ord(ch) == 0 for ch in data):
                    raise UnicodeDecodeError("utf-8", body, 0, 1, "nul")
            except UnicodeDecodeError:
                data = body
                encoding = "bytes"
            values.append({
                "id": attachment_id,
                "filename": str(entry["filename"]),
                "content_type": entry.get("content_type"),
                "byte_size": expected_size,
                "data": data,
                "encoding": encoding,
            })
            _context_accesses.append(attachment_id)
        except Exception as exc:
            raise ValueError("prepared context failed integrity verification") from exc
    if len(values) == 1 and values[0]["encoding"] == "utf-8":
        _namespace["context"] = values[0]["data"]
    else:
        _namespace.pop("context", None)
    return values


_namespace["_fleet_load_context_manifest"] = _fleet_load_context_manifest


class _OutputBuffer(io.StringIO):
    def __init__(self, execution_id, field):
        super().__init__()
        self._execution_id = execution_id
        self._field = field

    def write(self, value):
        remaining = _MAX_OUTPUT_CHARS - self.tell()
        if remaining > 0:
            super().write(value[:remaining])
        if value and self._execution_id:
            with _lock:
                state = _execution_outputs.get(self._execution_id)
                if state is not None:
                    current = state[self._field]
                    remaining = _MAX_OUTPUT_CHARS - len(current)
                    if remaining > 0:
                        state[self._field] = current + value[:remaining]
        return len(value)


def _execute(data):
    code = data.get("code")
    variables = data.get("variables") or {}
    execution_id = str(data.get("execution_id") or "")
    if not isinstance(code, str) or not isinstance(variables, dict):
        raise ValueError("execution request is invalid")
    if execution_id:
        with _lock:
            _execution_outputs[execution_id] = {"stdout": "", "stderr": "", "done": False}
    stdout = _OutputBuffer(execution_id, "stdout")
    stderr = _OutputBuffer(execution_id, "stderr")
    result = {"stdout": "", "stderr": "", "final": None, "error": None, "error_category": None}
    try:
        with _execution_lock:
            access_start = len(_context_accesses)
            _namespace.update({str(key): _decode_value(value) for key, value in variables.items()})
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exec(compile(code, "<fleet-rlm>", "exec"), _namespace, _namespace)
            except BaseException as exc:
                value = getattr(exc, "value", None)
                if type(exc).__name__ in {"FleetFinalOutputError", "_FleetFinalOutput"} and isinstance(value, dict):
                    result["final"] = value
                else:
                    result["error"] = str(exc)[:2000]
                    result["error_category"] = type(exc).__name__
            result["stdout"] = stdout.getvalue()[:_MAX_OUTPUT_CHARS]
            result["stderr"] = stderr.getvalue()[:_MAX_OUTPUT_CHARS]
            result["context_accesses"] = _context_accesses[access_start:]
        return result
    finally:
        if execution_id:
            with _lock:
                state = _execution_outputs.get(execution_id)
                if state is not None:
                    state["done"] = True


class _BrokerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _send_json(self, {"status": "ok"})
            return
        if parsed.path == "/output":
            secret = self.headers.get("X-Broker-Secret", "")
            if not hmac.compare_digest(secret, _BROKER_SECRET):
                _send_json(self, {"error": "unauthorized"}, 401)
                return
            qs = parse_qs(parsed.query)
            execution_id = str(qs.get("execution_id", [""])[0])
            try:
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except ValueError:
                offset = 0
            release = qs.get("release", ["0"])[0] == "1"
            with _lock:
                state = _execution_outputs.get(execution_id)
                if state is None:
                    _send_json(self, {"error": "unknown execution"}, 404)
                    return
                stdout = state["stdout"]
                stderr = state["stderr"]
                done = bool(state["done"])
                body = {
                    "stdout": stdout[offset:],
                    "stderr": stderr,
                    "done": done,
                    "next_offset": len(stdout),
                }
                if release and done:
                    _execution_outputs.pop(execution_id, None)
            _send_json(self, body)
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
        try:
            data = _read_json(self)
        except (ValueError, json.JSONDecodeError):
            _send_json(self, {"error": "invalid request"}, 400)
            return
        if parsed.path == "/execute":
            try:
                _send_json(self, _execute(data))
            except Exception:
                _send_json(self, {"error": "execution request failed"}, 400)
            return
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
        context_mount_root: str | None = None,
        context_manifest_sha256: str | None = None,
    ) -> None:
        self._sandbox = sandbox
        if not isinstance(broker_port, int) or isinstance(broker_port, bool) or not 0 < broker_port <= 65_535:
            raise ValueError(f"broker_port must be between 1 and 65535, got {broker_port!r}")
        self._broker_port = broker_port
        self._poll_interval_s = poll_interval_s
        if (context_mount_root is None) != (context_manifest_sha256 is None):
            raise ValueError("context binding must include both mount root and manifest digest")
        self._context_mount_root = context_mount_root
        self._context_manifest_sha256 = context_manifest_sha256
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
        server_code = (
            _BROKER_SERVER_CODE.replace("__BROKER_SECRET__", repr(self._broker_secret))
            .replace("__BROKER_PORT__", str(self._broker_port))
            .replace("__MAX_REQUEST_BYTES__", str(_MAX_EXECUTE_REQUEST_BYTES))
            .replace("__MAX_OUTPUT_CHARS__", str(_MAX_EXECUTE_OUTPUT_CHARS))
            .replace("__CONTEXT_MOUNT_ROOT__", repr(self._context_mount_root))
            .replace("__CONTEXT_MANIFEST_SHA256__", repr(self._context_manifest_sha256))
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

    def bind_context_manifest(self, *, trusted_mount_root: str, expected_manifest_sha256: str) -> None:
        """Bind host-authorized context before the broker process is started."""
        binding = (str(trusted_mount_root), str(expected_manifest_sha256))
        current = (self._context_mount_root, self._context_manifest_sha256)
        if current != (None, None) and current != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        if self._broker_url is not None and current != binding:
            raise DaytonaAdapterError(
                message="context manifest must be bound before broker startup",
                cause_type="ContextIntegrityError",
            )
        self._context_mount_root, self._context_manifest_sha256 = binding

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

    @staticmethod
    def _encode_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__fleet_type__": "bytes", "data": base64.b64encode(value).decode("ascii")}
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [DaytonaHttpToolBroker._encode_value(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): DaytonaHttpToolBroker._encode_value(item) for key, item in value.items()}
        raise DaytonaAdapterError(
            message="sandbox variable type is unsupported",
            cause_type="InterpreterVariableError",
        )

    def execute_code(
        self,
        code: str,
        variables: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Callable[[str], None] | None = None,
    ) -> BackendExecutionResult:
        """Execute one cell and optionally forward stdout while it is produced."""
        from fleet_rlm.daytona.interpreter import BackendExecutionResult

        self.ensure_started()
        if self._stopped:
            raise DaytonaAdapterError(message="broker already stopped", cause_type="InterpreterLifecycleError")
        execution_id = uuid.uuid4().hex if on_stdout is not None else None
        payload = {
            "code": code,
            "variables": {str(key): self._encode_value(value) for key, value in (variables or {}).items()},
        }
        if execution_id is not None:
            payload["execution_id"] = execution_id
        self._http()

        response_box: list[httpx.Response] = []
        request_errors: list[BaseException] = []

        def request() -> None:
            try:
                response_box.append(self._http().post("/execute", json=payload, timeout=timeout_s))
            except BaseException as exc:
                request_errors.append(exc)

        if on_stdout is None:
            request()
        else:
            thread = Thread(target=request, daemon=True)
            thread.start()
            offset = 0
            done = False
            while thread.is_alive():
                done, offset = self._poll_output(execution_id, offset, on_stdout)
                thread.join(timeout=self._poll_interval_s)
            thread.join()
            for _ in range(20):
                done, offset = self._poll_output(execution_id, offset, on_stdout)
                if done:
                    break
                time.sleep(self._poll_interval_s)
            self._poll_output(execution_id, offset, on_stdout, release=done)

        if request_errors:
            raise DaytonaAdapterError(
                message="sandbox execution request failed",
                cause_type="BrokerExecutionError",
            ) from request_errors[0]
        if not response_box:
            raise DaytonaAdapterError(
                message="sandbox execution produced no response", cause_type="BrokerExecutionError"
            )
        response = response_box[0]
        if response.status_code != 200:
            raise DaytonaAdapterError(
                message=f"sandbox execution failed with HTTP {response.status_code}",
                cause_type="BrokerExecutionError",
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise DaytonaAdapterError(
                message="sandbox execution returned an invalid response",
                cause_type="BrokerExecutionError",
            ) from exc
        final = result.get("final")
        accesses = tuple(str(value) for value in result.get("context_accesses") or ())
        return BackendExecutionResult(
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            final=dict(final) if isinstance(final, dict) else None,
            error=str(result.get("error") or "") or None,
            error_category=str(result.get("error_category") or "") or None,
            context_accesses=accesses,
        )

    def _poll_output(
        self,
        execution_id: str | None,
        offset: int,
        on_stdout: Callable[[str], None],
        *,
        release: bool = False,
    ) -> tuple[bool, int]:
        if execution_id is None:
            return False, offset
        try:
            response = self._http().get(
                "/output",
                params={
                    "execution_id": execution_id,
                    "offset": str(offset),
                    "release": "1" if release else "0",
                },
                timeout=5,
            )
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return False, offset
        if response.status_code != 200:
            return False, offset
        try:
            result = response.json()
        except ValueError:
            return False, offset
        stdout = str(result.get("stdout") or "")
        if stdout:
            on_stdout(stdout)
        try:
            next_offset = max(offset, int(result.get("next_offset", offset)))
        except (TypeError, ValueError):
            next_offset = offset + len(stdout)
        return bool(result.get("done")), next_offset

    def execute_with_callbacks(
        self,
        *,
        run_code: Callable[[], str | BackendExecutionResult],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> BackendExecutionResult:
        """
        Execute sandbox code while servicing its tool callbacks.

        Parameters:
            run_code (Callable[[], str | BackendExecutionResult]): Code execution callable.
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes a requested tool.

        Returns:
            BackendExecutionResult: The execution result, including captured output and any extracted final payload.

        Raises:
            DaytonaAdapterError: If the broker is stopped, execution fails, or produces no result.
        """
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

    def stop(self, *, strict: bool = False) -> None:
        """
        Stop the broker and release its HTTP client and Daytona session.

        Parameters:
            strict (bool): Whether to re-raise the first cleanup error after all cleanup steps complete.
        """
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
        # Strict mode still runs every disposal step; the first failure is
        # recorded and re-raised only after the remaining steps have run.
        first_error: BaseException | None = None
        if client is not None:
            if strict:
                try:
                    client.close()
                except BaseException as exc:
                    first_error = exc
            else:
                with contextlib.suppress(Exception):
                    client.close()
        if session_id is not None:
            if strict:
                try:
                    self._sandbox.process.delete_session(session_id)
                except BaseException as exc:
                    first_error = first_error or exc
            else:
                with contextlib.suppress(Exception):
                    self._sandbox.process.delete_session(session_id)
        if first_error is not None:
            raise first_error

    def _wait_health(self, *, timeout_s: float) -> None:
        """Wait for the broker health endpoint to become ready.

        Parameters:
            timeout_s (float): Maximum time to wait for a successful health check.

        Raises:
            DaytonaAdapterError: If authentication fails or the broker does not become healthy before the timeout.
        """
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
        """
        Poll for pending broker requests and fulfill them concurrently.

        Parameters:
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes each requested tool.

        Returns:
            bool: `True` if pending requests were found and processed, `False` otherwise.

        Raises:
            DaytonaAdapterError: If the broker responds with a non-success, non-server-error status.
        """
        assert self._broker_url is not None
        self._poll_count += 1
        try:
            response = self._http().get("/pending", params={"max": "8"}, timeout=5)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return False
        if response.status_code != 200:
            # 5xx from the preview proxy is a transient failure mode the poll
            # loops recover from, exactly like the tolerated transport errors
            # above; only non-recoverable statuses abort the execution.
            if response.status_code >= 500:
                return False
            raise DaytonaAdapterError(
                message=f"broker poll failed with HTTP {response.status_code}",
                cause_type="BrokerPollError",
            )
        try:
            payload = response.json()
        except ValueError:
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
