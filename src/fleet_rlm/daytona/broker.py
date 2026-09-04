"""Canonical Daytona broker boundary.

This module owns the source protocol, HTTP-in-sandbox host-tool transport,
and synchronous DSPy bridge.  It deliberately contains no process-global
service loop or provider lifecycle policy; composition injects a
``SyncBridgeDispatcher`` for each runtime.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import inspect
import json
import keyword
import logging
import math
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Lock, Thread
from typing import TYPE_CHECKING, Any

import httpx
from daytona import SessionExecuteRequest

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_sandbox_not_found,
    is_transient_provider_failure,
    map_provider_error,
    provider_status_code,
    sanitize_provider_message,
)
from fleet_rlm.json_types import validate_json_value

if TYPE_CHECKING:
    from fleet_rlm.daytona.interpreter import BackendExecutionResult


FINAL_OUTPUT_MARKER = "__FLEET_FINAL_OUTPUT__"


def build_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    body = _typed_submit_source(output_fields) if output_fields else _generic_submit_source()
    return f"{_strict_submit_helpers_source()}\n\n{body}"


def remote_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    """Generate remote setup source code for submitting final tool output.

    Parameters:
        output_fields (list[dict[str, Any]] | None): Output field definitions used to
            generate the typed submission function; `None` or an empty list produces
            the generic submission function.

    Returns:
        str: Complete remote setup source code.
    """
    return f"""
import base64 as _base64
import json
_json = json
FINAL_OUTPUT_MARKER = {FINAL_OUTPUT_MARKER!r}

class FleetFinalOutputError(Exception):
    def __init__(self, value):
        self.value = value
        super().__init__("Final output submitted")

{build_submit_setup_code(output_fields)}
""".strip()


def reset_binding_source(tool_names: list[str] | tuple[str, ...]) -> str:
    """Remove all prior host bindings before installing a new invocation set."""
    names = repr(tuple(sorted(set(tool_names))))
    return f"""
for _fleet_name in {names}:
    globals().pop(_fleet_name, None)
globals().pop("SUBMIT", None)
""".strip()


def _generic_submit_source() -> str:
    return """
import base64 as _base64

def SUBMIT(**kwargs):
    _fleet_validate_json(kwargs)
    payload = _base64.b64encode(
        _json.dumps(kwargs, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).decode("ascii")
    print(f"{FINAL_OUTPUT_MARKER}{payload}{FINAL_OUTPUT_MARKER}")
    raise FleetFinalOutputError(kwargs)
""".strip()


def _typed_submit_source(output_fields: list[dict[str, Any]]) -> str:
    """
    Generate source code for a typed ``SUBMIT`` function based on configured output fields.

    Parameters:
        output_fields (list[dict[str, Any]]): Output field definitions used to build the
            generated signature and result mapping.

    Returns:
        str: Source code defining the generated ``SUBMIT`` function.
    """
    signature_parts: list[str] = []
    validation_parts: list[str] = []
    result_parts: list[str] = []
    default_values: dict[str, str] = {}
    ordered_fields = [
        *[field for field in output_fields if bool(field.get("required", True))],
        *[field for field in output_fields if not bool(field.get("required", True))],
    ]
    for field in ordered_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        type_hint = str(field.get("type") or "").strip()
        required = bool(field.get("required", True))
        parameter = f"{name}: {type_hint}" if type_hint else name
        if not required:
            parameter += "=_FLEET_MISSING"
            default_json = field.get("default_json")
            if not isinstance(default_json, str):
                raise ValueError(f"typed output default for {name} is not JSON-compatible")
            default_values[name] = default_json
        signature_parts.append(parameter)
        if type_hint in {"str", "builtins.str"}:
            message = (
                f"SUBMIT field {name} must be a string; serialize mappings/lists with "
                "json.dumps(value, ensure_ascii=False)"
            )
            validation_parts.extend(
                (
                    f"if not isinstance({name}, str):",
                    f"    raise TypeError({message!r})",
                )
            )
        if not required:
            validation_parts.extend(
                (
                    f"if {name} is _FLEET_MISSING:",
                    f"    {name} = _fleet_default({name!r})",
                )
            )
        result_parts.append(f'"{name}": {name}')
    signature = ", ".join(signature_parts) or "**kwargs"
    body_lines = [
        *validation_parts,
        f"result = {{{', '.join(result_parts)}}}" if result_parts else "result = dict(kwargs)",
    ]
    body = "\n    ".join(body_lines)
    defaults = repr(default_values)
    return f"""
import base64 as _base64

_FLEET_MISSING = object()
_FLEET_DEFAULTS = {defaults}

def _fleet_default(name):
    return _json.loads(_FLEET_DEFAULTS[name])

def SUBMIT({signature}):
    {body}
    _fleet_validate_json(result)
    payload = _base64.b64encode(
        _json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).decode("ascii")
    print(f"{{FINAL_OUTPUT_MARKER}}{{payload}}{{FINAL_OUTPUT_MARKER}}")
    raise FleetFinalOutputError(result)
""".strip()


def _strict_submit_helpers_source() -> str:
    """Return private remote helpers for strict JSON submission."""
    return """
def _fleet_validate_json(value):
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not __import__("math").isfinite(value):
            raise TypeError("SUBMIT contains a non-finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("SUBMIT contains a non-string mapping key")
            _fleet_validate_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _fleet_validate_json(item)
        return
    raise TypeError("SUBMIT contains an unsupported value")
""".strip()


def extract_final_payload(stdout: str, *, marker: str = FINAL_OUTPUT_MARKER) -> dict[str, Any] | None:
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


def final_output_frame(value: Mapping[str, Any], *, marker: str = FINAL_OUTPUT_MARKER) -> str:
    """Return the exact private stdout frame emitted by ``SUBMIT``."""
    validate_json_value(value, path="SUBMIT")
    encoded = base64.b64encode(json.dumps(dict(value), ensure_ascii=False, allow_nan=False).encode("utf-8")).decode(
        "ascii"
    )
    return f"{marker}{encoded}{marker}"


BROKER_SERVER_CODE = """
import base64
import contextlib
import hashlib
import hmac
import io
import json
import math
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_pending_condition = threading.Condition(_lock)
_output_condition = threading.Condition(_lock)
_pending_requests = {}
_results = {}
_execution_outputs = {}
_BROKER_SECRET = __BROKER_SECRET__
_MAX_REQUEST_BYTES = __MAX_REQUEST_BYTES__
_MAX_OUTPUT_CHARS = __MAX_OUTPUT_CHARS__
_MAX_LONG_POLL_WAIT_S = 0.25
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
    body = json.dumps(data, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _bounded_wait_seconds(qs):
    try:
        value = float(qs.get("wait", ["0"])[0])
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(_MAX_LONG_POLL_WAIT_S, value))


def _pending_available():
    return any(
        not req.get("lease_token") and not req.get("completed")
        for req in _pending_requests.values()
    )


def _output_available(execution_id, offset):
    state = _execution_outputs.get(execution_id)
    return (
        state is None
        or bool(state["done"])
        or len(state["stdout"]) > offset
    )


def _decode_value(value):
    if isinstance(value, dict) and value.get("__fleet_type__") == "bytes":
        return base64.b64decode(str(value.get("data") or ""), validate=True)
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("unsupported variable mapping key")
        return {key: _decode_value(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("unsupported non-finite variable")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("unsupported variable value")


def _validate_json_value(value):
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("unsupported non-finite value")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported mapping key")
            _validate_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    raise ValueError("unsupported value")


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


# Only expose the context loader when a context capsule is actually bound.
# An unbound, always-failing loader invites models to waste iterations probing
# a dead discovery API.
if _CONTEXT_MOUNT_ROOT is not None and _CONTEXT_MANIFEST_SHA256 is not None:
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
            with _output_condition:
                state = _execution_outputs.get(self._execution_id)
                if state is not None:
                    current = state[self._field]
                    remaining = _MAX_OUTPUT_CHARS - len(current)
                    if remaining > 0:
                        state[self._field] = current + value[:remaining]
                        _output_condition.notify_all()
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
            with _output_condition:
                state = _execution_outputs.get(execution_id)
                if state is not None:
                    state["done"] = True
                    _output_condition.notify_all()


class _BrokerHandler(BaseHTTPRequestHandler):
    # Keep the pooled host client connection reusable across poll requests.
    protocol_version = "HTTP/1.1"

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
            wait_s = _bounded_wait_seconds(qs)
            waited_ms = 0
            with _output_condition:
                state = _execution_outputs.get(execution_id)
                if state is None:
                    body = {"error": "unknown execution"}
                    status = 404
                else:
                    if wait_s > 0 and not _output_available(execution_id, offset):
                        wait_started_ns = time.monotonic_ns()
                        _output_condition.wait_for(
                            lambda: _output_available(execution_id, offset),
                            timeout=wait_s,
                        )
                        waited_ms = max(0, int((time.monotonic_ns() - wait_started_ns) / 1_000_000))
                        state = _execution_outputs.get(execution_id)
                    if state is None:
                        body = {"error": "unknown execution"}
                        status = 404
                    else:
                        stdout = state["stdout"]
                        stderr = state["stderr"]
                        done = bool(state["done"])
                        body = {
                            "stdout": stdout[offset:],
                            "stderr": stderr,
                            "done": done,
                            "next_offset": len(stdout),
                            "waited_ms": waited_ms,
                        }
                        status = 200
                        if release and done:
                            _execution_outputs.pop(execution_id, None)
            _send_json(self, body, status)
            return
        if parsed.path == "/pending":
            secret = self.headers.get("X-Broker-Secret", "")
            if not hmac.compare_digest(secret, _BROKER_SECRET):
                _send_json(self, {"error": "unauthorized"}, 401)
                return
            qs = parse_qs(parsed.query)
            try:
                max_items = max(1, int(qs.get("max", ["1"])[0]))
            except ValueError:
                max_items = 1
            out = []
            wait_s = _bounded_wait_seconds(qs)
            waited_ms = 0
            with _pending_condition:
                if wait_s > 0:
                    wait_started_ns = time.monotonic_ns()
                    _pending_condition.wait_for(_pending_available, timeout=wait_s)
                    waited_ms = max(0, int((time.monotonic_ns() - wait_started_ns) / 1_000_000))
                for call_id, req in list(_pending_requests.items()):
                    if req.get("lease_token") or req.get("completed"):
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
            _send_json(self, {"requests": out, "waited_ms": waited_ms})
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
            # _read_json rejects oversized bodies before consuming them. Close
            # this HTTP/1.1 connection so the unread bytes cannot be parsed as
            # a second request on the pooled connection.
            self.close_connection = True
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
            with _pending_condition:
                _pending_requests[call_id] = {
                    "tool_name": data.get("tool_name"),
                    "args": data.get("args") or [],
                    "kwargs": data.get("kwargs") or {},
                    "lease_token": None,
                    "completed": False,
                    "event": event,
                }
                _pending_condition.notify_all()
            if not event.wait(timeout=180.0):
                with _lock:
                    _pending_requests.pop(call_id, None)
                    _results.pop(call_id, None)
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
            response_body = {"status": "ok"}
            response_status = 200
            event = None
            with _lock:
                req = _pending_requests.get(call_id)
                if req is None:
                    response_body = {"error": "unknown call"}
                    response_status = 404
                elif req.get("completed"):
                    response_body = {"error": "stale claim"}
                    response_status = 409
                elif claim != req.get("lease_token"):
                    response_body = {"error": "stale claim"}
                    response_status = 409
                elif "error" in data:
                    _results[call_id] = {"error": data.get("error")}
                    req["completed"] = True
                    event = req.get("event")
                else:
                    result = data.get("result")
                    try:
                        _validate_json_value(result)
                    except ValueError:
                        response_body = {"error": "tool result is not JSON-compatible"}
                        response_status = 422
                    else:
                        _results[call_id] = result
                        req["completed"] = True
                        event = req.get("event")
            if event is not None:
                event.set()
            _send_json(self, response_body, response_status)
            return
        _send_json(self, {"error": "not found"}, 404)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    server = _ThreadedHTTPServer(("0.0.0.0", __BROKER_PORT__), _BrokerHandler)
    server.serve_forever()
""".strip()

TOOL_WRAPPER_TEMPLATE = """
def {tool_name}({signature}):
    import json as _json
    import urllib.error as _urllib_error
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
    try:
        with _urllib_request.urlopen(req, timeout=130) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except _urllib_error.HTTPError as _exc:
        # The broker answers host-tool failures with HTTP 500 plus an
        # ``{{"error": "<safe message>"}}`` body; urllib raises before reading
        # it, so surface the safe message instead of a bare status line.
        try:
            _error_message = _json.loads(_exc.read().decode("utf-8")).get("error")
        except Exception:
            _error_message = None
        if _error_message:
            raise RuntimeError(f"Tool call failed: {{_error_message}}") from None
        raise RuntimeError(f"Tool call failed: HTTP {{_exc.code}}") from None
    if "error" in data:
        raise RuntimeError(f"Tool call failed: {{data['error']}}")
    return data.get("result")
""".strip()
logger = logging.getLogger(__name__)

DEFAULT_BROKER_PORT = 3000

_PREVIEW_LINK_RETRY_DELAYS = (0.25, 0.5)

_BROKER_SERVER_PATH = "/home/daytona/fleet_rlm_broker_server.py"

_BROKER_SESSION_COMMAND = f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"

_MAX_EXECUTE_REQUEST_BYTES = 2 * 1024 * 1024

_MAX_EXECUTE_OUTPUT_CHARS = 64 * 1024

_MAX_PENDING_POLL_INTERVAL_S = 0.25

_MAX_PENDING_POLL_BACKOFF_EXPONENT = 30

_MAX_CALLBACK_WORKERS = 8

_EXECUTION_STAT_KEYS = (
    "poll_count",
    "empty_poll_count",
    "poll_error_count",
    "poll_latency_ms",
    "poll_latency_max_ms",
    "pending_batch_count",
    "pending_request_count",
    "callback_dispatch_count",
    "callback_dispatch_ms",
    "callback_dispatch_max_ms",
    "tool_execution_ms",
    "tool_execution_max_ms",
    "result_post_count",
    "result_post_failures",
    "result_post_ms",
    "result_post_max_ms",
    "pending_wait_requested_ms",
    "pending_wait_elapsed_ms",
    "drain_poll_count",
    "callback_executor_created",
    "callback_executor_reused",
    "tool_call_count",
    "output_poll_count",
    "output_poll_failures",
    "output_poll_latency_ms",
    "output_poll_latency_max_ms",
    "output_release_count",
    "output_chars",
    "output_wait_requested_ms",
    "output_wait_elapsed_ms",
    "execution_wall_ms",
    "run_code_ms",
)


class FleetFinalOutputError(Exception):
    """Raised inside an interpreter when SUBMIT completes successfully."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        super().__init__("Final output submitted")


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
        """
        Initialize a Daytona HTTP tool broker.

        Parameters:
            broker_port (int): Port used by the sandbox-hosted broker.
            context_mount_root (str | None): Root directory for bound context data.
            context_manifest_sha256 (str | None): SHA-256 digest of the bound context manifest.

        Raises:
            ValueError: If the broker port is outside the range 1-65535 or only one
                context-binding value is provided.
        """
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
        self._callback_executor: ThreadPoolExecutor | None = None
        self._callback_executor_lock = Lock()
        self._metrics_lock = Lock()
        self._active_execution_stats: dict[str, int] | None = None
        self.last_execution_stats: dict[str, int] = {}

    @staticmethod
    def _new_execution_stats() -> dict[str, int]:
        """
        Create a zero-initialized execution statistics mapping.
        """
        return {key: 0 for key in _EXECUTION_STAT_KEYS}

    def _begin_execution_stats(self) -> tuple[dict[str, int], bool]:
        """Start one metrics scope, reusing the outer scope for nested cells."""
        with self._metrics_lock:
            if self._active_execution_stats is not None:
                return self._active_execution_stats, False
            stats = self._new_execution_stats()
            self._active_execution_stats = stats
            return stats, True

    def _finish_execution_stats(self, stats: dict[str, int], *, owner: bool) -> None:
        """Finalize and publish statistics for an execution owned by this broker."""
        if not owner:
            return
        with self._metrics_lock:
            if self._active_execution_stats is stats:
                self._active_execution_stats = None
            self.last_execution_stats = dict(stats)

    def _record_metric(self, key: str, value: int = 1) -> None:
        """Record a metric value for the active execution, if one exists.

        Parameters:
            key (str): The metric name.
            value (int): The amount to add to the metric.
        """
        with self._metrics_lock:
            stats = self._active_execution_stats
            if stats is not None:
                stats[key] = stats.get(key, 0) + int(value)

    def _record_metric_max(self, key: str, value: int) -> None:
        """Record the maximum observed value for a metric during the active execution."""
        with self._metrics_lock:
            stats = self._active_execution_stats
            if stats is not None:
                stats[key] = max(stats.get(key, 0), int(value))

    def _record_duration(self, key: str, started_ns: int, *, max_key: str | None = None) -> int:
        """
        Record the elapsed time since a start timestamp and return it in milliseconds.

        Parameters:
            key (str): Metric key for the elapsed duration.
            started_ns (int): Start timestamp from `time.perf_counter_ns()`.
            max_key (str | None): Optional metric key used to record the maximum duration.

        Returns:
            int: Elapsed duration in milliseconds.
        """
        elapsed_ms = max(0, int((time.perf_counter_ns() - started_ns) / 1_000_000))
        self._record_metric(key, elapsed_ms)
        if max_key is not None:
            self._record_metric_max(max_key, elapsed_ms)
        return elapsed_ms

    def _poll_backoff_delay(self, empty_polls: int) -> float:
        """Return a bounded exponential delay after consecutive empty polls."""
        base = float(self._poll_interval_s)
        if math.isnan(base) or base <= 0:
            return 0.0
        if math.isinf(base):
            return _MAX_PENDING_POLL_INTERVAL_S
        base = min(base, _MAX_PENDING_POLL_INTERVAL_S)
        exponent = min(max(empty_polls - 1, 0), _MAX_PENDING_POLL_BACKOFF_EXPONENT)
        return min(_MAX_PENDING_POLL_INTERVAL_S, math.ldexp(base, exponent))

    def _get_callback_executor(self) -> tuple[ThreadPoolExecutor, bool]:
        """
        Obtain the broker-owned callback executor, creating it on first use.

        Returns:
            tuple[ThreadPoolExecutor, bool]: The callback executor and whether it was
            created by this call.

        Raises:
            DaytonaAdapterError: If the broker has already stopped.
        """
        with self._callback_executor_lock:
            if self._stopped:
                raise DaytonaAdapterError(message="broker already stopped", cause_type="InterpreterLifecycleError")
            if self._callback_executor is None:
                self._callback_executor = ThreadPoolExecutor(
                    max_workers=_MAX_CALLBACK_WORKERS,
                    thread_name_prefix="fleet-daytona-tool",
                )
                return self._callback_executor, True
            return self._callback_executor, False

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
            BROKER_SERVER_CODE.replace("__BROKER_SECRET__", repr(self._broker_secret))
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
        # Retain the session immediately after creation. Startup command
        # failure must not strand a provider session that ``stop`` still owns.
        self._broker_session_id = session_id
        self._sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(command=_BROKER_SESSION_COMMAND, run_async=True),
        )
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
                if not is_transient_provider_failure(mapped):
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
        self._pending_wrappers.append(reset_binding_source(tuple(self._injected_tools)))
        self._injected_tools.clear()
        for name, fn in tools.items():
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
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise DaytonaAdapterError(
                    message="sandbox variable type is unsupported",
                    cause_type="InterpreterVariableError",
                )
            return value
        if isinstance(value, (list, tuple)):
            return [DaytonaHttpToolBroker._encode_value(item) for item in value]
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise DaytonaAdapterError(
                    message="sandbox variable type is unsupported",
                    cause_type="InterpreterVariableError",
                )
            return {key: DaytonaHttpToolBroker._encode_value(item) for key, item in value.items()}
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
        """
        Execute code in the sandbox and optionally forward stdout as it is produced.

        Parameters:
            code (str): Code to execute.
            variables (Mapping[str, Any] | None): Variables to make available during execution.
            timeout_s (float): Maximum time to wait for the execution request.
            on_stdout (Callable[[str], None] | None): Callback invoked with streamed stdout chunks.

        Returns:
            BackendExecutionResult: Execution output, final result, errors, and accessed context paths.

        Raises:
            DaytonaAdapterError: If the broker is stopped or the execution
                request, response, or variables are invalid.
        """
        from fleet_rlm.daytona.interpreter import BackendExecutionResult

        self.ensure_started()
        if self._stopped:
            raise DaytonaAdapterError(message="broker already stopped", cause_type="InterpreterLifecycleError")
        execution_id = uuid.uuid4().hex if on_stdout is not None else None
        raw_variables = variables or {}
        if any(not isinstance(key, str) for key in raw_variables):
            raise DaytonaAdapterError(
                message="sandbox variable type is unsupported",
                cause_type="InterpreterVariableError",
            )
        payload = {
            "code": code,
            "variables": {key: self._encode_value(value) for key, value in raw_variables.items()},
        }
        if execution_id is not None:
            payload["execution_id"] = execution_id
        self._http()
        stats, stats_owner = self._begin_execution_stats()
        wall_started_ns = time.perf_counter_ns()

        try:
            response_box: list[httpx.Response] = []
            request_errors: list[Exception] = []

            def request() -> None:
                """
                Submit the execution payload and record either the HTTP response or the encountered exception.
                """
                try:
                    response_box.append(self._http().post("/execute", json=payload, timeout=timeout_s))
                except Exception as exc:
                    request_errors.append(exc)

            if on_stdout is None:
                request()
            else:
                thread = Thread(target=request, daemon=True)
                thread.start()
                offset = 0
                empty_polls = 0
                while thread.is_alive():
                    previous_offset = offset
                    wait_s = 0.0 if empty_polls == 0 else self._poll_backoff_delay(empty_polls)
                    poll_started_ns = time.perf_counter_ns()
                    done, offset = self._poll_output(execution_id, offset, on_stdout, wait_s=wait_s)
                    if done:
                        thread.join()
                        break
                    # Fresh output is a signal to keep the next check quick;
                    # repeated empty checks use a bounded server-side wait to
                    # reduce per-cell HTTP overhead while a long-running cell
                    # is quiet.
                    if offset > previous_offset:
                        empty_polls = 0
                    else:
                        empty_polls += 1
                        self._sleep_remaining_poll_delay(wait_s, poll_started_ns)
                thread.join()
                # The broker's /execute response is emitted only after the
                # server marks this execution done. One release read is enough
                # to flush any final stdout and discard the remote buffer; the
                # old 20-poll drain only repeated already-settled requests.
                self._poll_output(execution_id, offset, on_stdout, release=True)

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
        finally:
            if stats_owner:
                self._record_duration("execution_wall_ms", wall_started_ns)
                self._finish_execution_stats(stats, owner=True)

    def _poll_output(
        self,
        execution_id: str | None,
        offset: int,
        on_stdout: Callable[[str], None],
        *,
        release: bool = False,
        wait_s: float = 0.0,
    ) -> tuple[bool, int]:
        """
        Polls the broker for newly available execution output and forwards it to the callback.

        Parameters:
            execution_id (str | None): Identifier of the execution whose output is being polled.
            offset (int): Current output position from which to read.
            on_stdout (Callable[[str], None]): Callback invoked with newly available standard output.
            release (bool): Whether completed output may be released after polling.
            wait_s (float): Maximum duration to wait for additional output.

        Returns:
            tuple[bool, int]: Whether execution output is complete and the next output position.
        """
        if execution_id is None:
            return False, offset
        if not math.isfinite(wait_s):
            wait_s = 0.0
        wait_s = max(0.0, min(_MAX_PENDING_POLL_INTERVAL_S, wait_s))
        poll_started_ns = time.perf_counter_ns()
        self._record_metric("output_poll_count")
        if release:
            self._record_metric("output_release_count")
        self._record_metric("output_wait_requested_ms", max(0, int(wait_s * 1_000)))
        params = {
            "execution_id": execution_id,
            "offset": str(offset),
            "release": "1" if release else "0",
        }
        if wait_s > 0:
            params["wait"] = f"{wait_s:.3f}"
        try:
            response = self._http().get(
                "/output",
                params=params,
                timeout=5,
            )
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            self._record_metric("output_poll_failures")
            self._record_duration(
                "output_poll_latency_ms",
                poll_started_ns,
                max_key="output_poll_latency_max_ms",
            )
            return False, offset
        self._record_duration(
            "output_poll_latency_ms",
            poll_started_ns,
            max_key="output_poll_latency_max_ms",
        )
        if response.status_code != 200:
            self._record_metric("output_poll_failures")
            return False, offset
        try:
            result = response.json()
        except ValueError:
            self._record_metric("output_poll_failures")
            return False, offset
        try:
            waited_ms = max(0, int(result.get("waited_ms", 0)))
        except (TypeError, ValueError):
            waited_ms = 0
        self._record_metric("output_wait_elapsed_ms", waited_ms)
        stdout = str(result.get("stdout") or "")
        if stdout:
            self._record_metric("output_chars", len(stdout))
            on_stdout(stdout)
        try:
            next_offset = max(offset, int(result.get("next_offset", offset)))
        except (TypeError, ValueError):
            next_offset = offset + len(stdout)
        return bool(result.get("done")), next_offset

    @staticmethod
    def _sleep_remaining_poll_delay(wait_s: float, started_ns: int) -> None:
        remaining_s = wait_s - (time.perf_counter_ns() - started_ns) / 1_000_000_000
        if remaining_s > 0:
            time.sleep(remaining_s)

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

        stats, stats_owner = self._begin_execution_stats()
        wall_started_ns = time.perf_counter_ns()
        fulfilled_start = self._fulfilled_count
        try:
            bucket: list[str | BackendExecutionResult | BaseException] = []

            def _runner() -> None:
                run_started_ns = time.perf_counter_ns()
                try:
                    bucket.append(run_code())
                except Exception as exc:
                    bucket.append(exc)
                finally:
                    self._record_duration("run_code_ms", run_started_ns)

            thread = Thread(target=_runner, daemon=True)
            thread.start()
            empty_polls = 0
            while thread.is_alive():
                if self._stopped:
                    break
                wait_s = 0.0 if empty_polls == 0 else self._poll_backoff_delay(empty_polls)
                poll_started_ns = time.perf_counter_ns()
                if self._poll_once(tool_executor, wait_s=wait_s):
                    # A fulfilled callback is progress. Poll again immediately
                    # so a sequence of model/tool calls does not pay an extra
                    # fixed sleep after every useful broker response.
                    empty_polls = 0
                    continue
                empty_polls += 1
                self._sleep_remaining_poll_delay(wait_s, poll_started_ns)
            thread.join(timeout=1.0)
            for _ in range(5):
                if self._stopped:
                    break
                self._record_metric("drain_poll_count")
                if not self._poll_once(tool_executor):
                    break

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
        finally:
            if stats_owner:
                self._record_metric("tool_call_count", self._fulfilled_count - fulfilled_start)
                self._record_duration("execution_wall_ms", wall_started_ns)
                self._finish_execution_stats(stats, owner=True)

    def stop(self, *, strict: bool = False) -> bool:
        """
        Stop the broker and release its callback workers, HTTP client, and Daytona session.

        Parameters:
            strict (bool): Whether to raise the first cleanup error encountered.

        Returns:
            bool: True if cleanup is settled, or False if a resource remains for a later cleanup attempt.
        """
        self._stopped = True
        session_id = self._broker_session_id
        self._broker_session_id = None
        # Strict mode records the first cleanup failure and re-raises it after
        # the remaining safe disposal steps have run. If callback workers
        # cannot be joined, later disposal would race their pooled client.
        first_error: BaseException | None = None
        settled = True

        # Callback workers may still be posting tool results through the pooled
        # HTTP client. Drain them before closing that client so no worker can
        # outlive broker shutdown or race a client disposal. Keep the executor
        # (and all other cleanup ownership) when shutdown fails so a later
        # ``stop`` call can retry it.
        executor = self._callback_executor
        if executor is not None:
            try:
                with self._callback_executor_lock:
                    executor.shutdown(wait=True)
                    if self._callback_executor is executor:
                        self._callback_executor = None
            except Exception as exc:
                settled = False
                if strict:
                    first_error = exc

        if not settled:
            if session_id is not None:
                self._broker_session_id = session_id
            if first_error is not None:
                raise first_error
            return False

        self._broker_url = None
        self._broker_token = None
        # Swap before closing so a concurrent late _http() caller never
        # observes the client mid-close; in-flight requests on the detached
        # client fail into the suppressed httpx error paths.
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except BaseException as exc:
                # Keep failed cleanup ownership attached to the broker so a
                # later stop() can retry the same resource. Non-strict mode
                # suppresses the error, but never silently drops ownership.
                self._client = client
                settled = False
                if strict:
                    first_error = exc
        if session_id is not None:
            # Sandbox deletion propagates asynchronously provider-side, so a
            # session delete racing it can surface as transient 5xx before the
            # final 404. Retry through that window; once the Sandbox is gone the
            # session is definitionally deleted with it.
            delete_error: BaseException | None = None
            for attempt, retry_delay in enumerate((0.0, 2.0, 4.0, 8.0)):
                if retry_delay:
                    time.sleep(retry_delay)
                try:
                    self._sandbox.process.delete_session(session_id)
                except BaseException as exc:
                    if _is_session_delete_settled(exc):
                        # The sandbox (and with it the session) is already gone,
                        # stopped, or unreachable, so the desired end state is
                        # reached; treat it as deleted instead of retrying
                        # against a torn-down Sandbox.
                        delete_error = None
                        break
                    delete_error = exc
                    if not is_transient_provider_failure(exc) or attempt == 3:
                        break
                else:
                    delete_error = None
                    break
            if delete_error is not None:
                # The session is still owned when deletion was rejected. The
                # broker remains stopped, so this retry cannot race startup.
                self._broker_session_id = session_id
                settled = False
                if strict:
                    first_error = first_error or delete_error
        if first_error is not None:
            raise first_error
        return settled

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

    def _poll_once(
        self,
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
        *,
        wait_s: float = 0.0,
    ) -> bool:
        """
        Poll for pending broker requests and fulfill them concurrently.

        Parameters:
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback used to
                execute each requested tool.
            wait_s (float): Maximum time to wait for pending requests before returning an empty result.

        Returns:
            bool: `True` if requests were found and fulfilled, `False` otherwise.

        Raises:
            DaytonaAdapterError: If the broker returns a non-success, non-server-error status.
        """
        assert self._broker_url is not None
        if not math.isfinite(wait_s):
            wait_s = 0.0
        wait_s = max(0.0, min(_MAX_PENDING_POLL_INTERVAL_S, wait_s))
        poll_started_ns = time.perf_counter_ns()
        self._poll_count += 1
        self._record_metric("poll_count")
        self._record_metric("pending_wait_requested_ms", max(0, int(wait_s * 1_000)))
        params = {"max": "8"}
        if wait_s > 0:
            params["wait"] = f"{wait_s:.3f}"
        try:
            response = self._http().get("/pending", params=params, timeout=5)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            self._record_metric("poll_error_count")
            self._record_duration(
                "poll_latency_ms",
                poll_started_ns,
                max_key="poll_latency_max_ms",
            )
            return False
        self._record_duration(
            "poll_latency_ms",
            poll_started_ns,
            max_key="poll_latency_max_ms",
        )
        if response.status_code != 200:
            # 5xx from the preview proxy is a transient failure mode the poll
            # loops recover from, exactly like the tolerated transport errors
            # above; only non-recoverable statuses abort the execution.
            if response.status_code >= 500:
                self._record_metric("poll_error_count")
                return False
            self._record_metric("poll_error_count")
            raise DaytonaAdapterError(
                message=f"broker poll failed with HTTP {response.status_code}",
                cause_type="BrokerPollError",
            )
        try:
            payload = response.json()
        except ValueError:
            self._record_metric("poll_error_count")
            return False
        try:
            waited_ms = max(0, int(payload.get("waited_ms", 0)))
        except (TypeError, ValueError):
            waited_ms = 0
        self._record_metric("pending_wait_elapsed_ms", waited_ms)
        requests_out = payload.get("requests") or []
        if not requests_out:
            self._record_metric("empty_poll_count")
            return False
        self._record_metric("pending_batch_count")
        self._record_metric("pending_request_count", len(requests_out))
        self._fulfilled_count += len(requests_out)
        # The broker polls on the interpreter thread, then fulfills host Tools
        # in worker threads. Copy the active Turn/MLflow context separately for
        # each request so Tool spans stay nested without sharing a Context.
        work = [(copy_context(), item) for item in requests_out]
        executor, created = self._get_callback_executor()
        self._record_metric("callback_executor_created" if created else "callback_executor_reused")
        self._record_metric("callback_dispatch_count", len(requests_out))
        dispatch_started_ns = time.perf_counter_ns()
        try:
            list(executor.map(lambda item: item[0].run(self._fulfill, item[1], tool_executor), work))
        finally:
            self._record_duration("callback_dispatch_ms", dispatch_started_ns, max_key="callback_dispatch_max_ms")
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
        tool_started_ns = time.perf_counter_ns()
        try:
            result = tool_executor(name, args, kwargs)
            validate_json_value(result, path=f"Tool {name} result")
            body: dict[str, Any] = {"id": call_id, "lease_token": lease, "result": result}
        except Exception as exc:
            message = sanitize_provider_message(str(exc))
            # One sanitized WARNING per host-tool failure; never log args,
            # kwargs, or tool content.
            logger.warning("host tool %s failed: %s", name or "<unknown>", message)
            body = {
                "id": call_id,
                "lease_token": lease,
                "error": message,
            }
        finally:
            self._record_duration("tool_execution_ms", tool_started_ns, max_key="tool_execution_max_ms")

        post_started_ns = time.perf_counter_ns()
        self._record_metric("result_post_count")
        try:
            response = self._http().post(
                "/result",
                content=json.dumps(body, allow_nan=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if response.status_code >= 400:
                self._record_metric("result_post_failures")
        except (httpx.HTTPError, TimeoutError, OSError):
            self._record_metric("result_post_failures")
        finally:
            self._record_duration("result_post_ms", post_started_ns, max_key="result_post_max_ms")

    def _preview_headers(self) -> dict[str, str]:
        """Build authentication headers for requests to the sandbox broker.

        Returns:
            dict[str, str]: Headers containing the broker secret and, when available, the Daytona preview token.
        """
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
            # Host Tools are kwargs-only callables: DSPy 3.3.x
            # ``RLM._make_interpreter_tool`` wraps every user tool in
            # ``def invoke(**kwargs)`` while spoofing this signature, so every
            # parameter must cross the wire by name. ``args`` is reserved for
            # POSITIONAL_ONLY parameters, which cannot be forwarded by name
            # (none exist on the current host tool surface).
            if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                args_list.append(name)
            else:
                kwargs_parts.append(f'"{name}": {name}')
        return TOOL_WRAPPER_TEMPLATE.format(
            tool_name=tool_name,
            signature=", ".join(sig_parts),
            args_list=", ".join(args_list),
            kwargs_dict=", ".join(kwargs_parts),
            broker_port=self._broker_port,
            broker_secret=self._broker_secret,
        )


def _is_session_delete_settled(exc: BaseException) -> bool:
    """Whether a failed session delete already reached the desired end state.

    Deleting a session inside a Sandbox races provider-side Sandbox teardown:
    depending on how far teardown progressed, the provider reports 404 (unknown
    Sandbox), 5xx (toolbox unreachable while the Sandbox is being deleted), or
    400 (no container IP because the Sandbox is no longer started).  In all of
    those states the session cannot outlive its Sandbox, so the delete is
    treated as settled instead of failing the whole strict cleanup.
    """
    if is_sandbox_not_found(exc):
        return True
    if getattr(exc, "status_code", None) == 400:
        message = str(exc)
        return "resolve container IP" in message or "Is the Sandbox started?" in message
    return False


_BRIDGE_SERVICE_POLL_S = 0.5


class SyncBridgeDispatcher:
    """Composition-owned routing authority for sync-view SDK coroutines.

    Each Daytona composition owns exactly one dispatcher and injects it into
    every :func:`sync_sandbox` view it creates, so multiple app/test
    compositions in one process cannot overwrite each other's bridge
    authority. The dispatcher carries the same RC-7 guarantee as the legacy
    module-global: its registered loop never performs nested synchronous
    waits, so posted coroutines are always serviced. Closing a view
    tombstones only that view's ``_SyncBridgeLoop``; the dispatcher itself is
    shared composition state and must never be tombstoned per-view.
    """

    def __init__(self) -> None:
        self._service_loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Register the loop servicing sync-view SDK coroutines for this composition."""
        self._service_loop = loop

    def clear_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Unregister only when this dispatcher still routes onto ``loop``.

        A disposing composition can never clear another composition's loop:
        the identity check makes retreat behaviorally safe under overlapping
        lifespans.
        """
        if loop is not None and self._service_loop is not loop:
            return
        self._service_loop = None

    def service_loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the registered composition loop, if any."""
        return self._service_loop


class _SyncBridgeLoop:
    """Service-loop routing and close state for one synchronous Daytona bridge.

    Posted SDK coroutines run on the composition-owned service loop exposed by
    the injected dispatcher; when no dispatcher is injected (e.g. private-test
    compositions that never install the Daytona inventory) the bridge falls
    back to its caller-captured loop. The bridge owns no threads, so Turns cannot leak daemon threads;
    :meth:`close` tombstones the bridge so late calls fail typed-fast instead
    of posting to a service loop after lease release.
    """

    def __init__(
        self,
        *,
        caller_loop: asyncio.AbstractEventLoop | None,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        self._caller_loop = caller_loop
        self._dispatcher = dispatcher
        self._closed = False

    def _bridge_error(self, message: str) -> DaytonaAdapterError:
        return DaytonaAdapterError(message=message, cause_type="InterpreterBridgeError")

    def close(self) -> None:
        """Tombstone the bridge; further calls fail fast until start()."""
        self._closed = True

    def start(self) -> None:
        """Clear the close tombstone (survives close/reopen)."""
        self._closed = False

    def service_loop(self) -> asyncio.AbstractEventLoop | None:
        """Resolve the loop servicing this bridge: injected dispatcher first.

        Resolution order pins authority at view creation: an explicitly
        injected composition dispatcher, then the legacy process-default
        dispatcher, then the caller-captured loop (legacy/test fallback).
        """
        if self._dispatcher is not None:
            registered = self._dispatcher.service_loop()
            if registered is not None:
                return registered
        return self._caller_loop

    def run(self, awaitable: Any) -> Any:
        """Post one awaitable on the service loop and block until it settles."""
        if self._closed:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge is closed")
        loop = self.service_loop()
        if loop is None or loop.is_closed():
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge service loop is unavailable")
        try:
            future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        except RuntimeError as exc:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise self._bridge_error("synchronous Daytona bridge service loop is unavailable") from exc
        while True:
            try:
                return future.result(timeout=_BRIDGE_SERVICE_POLL_S)
            except TimeoutError:
                if loop.is_closed() or not loop.is_running():
                    future.cancel()
                    if inspect.iscoroutine(awaitable):
                        with contextlib.suppress(Exception):
                            awaitable.close()
                    raise self._bridge_error("synchronous Daytona bridge service loop stopped") from None


def _sync_await(
    awaitable: Any,
    owner: _SyncBridgeLoop,
    guard_loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    """Run one async SDK operation on the composition-wide bridge service loop.

    ``guard_loop`` anchors the legacy fail-fast contract: the loop a bridge
    was declared against (and the resolved service loop itself) may never call
    the bridge synchronously, because that loop's thread is the one that would
    have to service the call.
    """
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if current_loop is not None and (current_loop is guard_loop or current_loop is owner.service_loop()):
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge called from its owning event loop",
            cause_type="InterpreterThreadError",
        )
    if not inspect.isawaitable(awaitable):
        raise DaytonaAdapterError(
            message="synchronous Daytona bridge requires an async SDK operation",
            cause_type="InterpreterBridgeContractError",
        )
    return owner.run(awaitable)


class _SyncCodeInterpreter:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def create_context(self, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_context(**kwargs), self._owner, self._guard_loop)

    def run_code(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.run_code(code, **kwargs), self._owner, self._guard_loop)

    def delete_context(self, context: Any, **kwargs: Any) -> None:
        _sync_await(self._service.delete_context(context, **kwargs), self._owner, self._guard_loop)


class _SyncProcess:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def code_run(self, code: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.code_run(code, **kwargs), self._owner, self._guard_loop)

    def create_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.create_session(session_id, **kwargs), self._owner, self._guard_loop)

    def execute_session_command(self, session_id: str, request: Any, **kwargs: Any) -> Any:
        return _sync_await(
            self._service.execute_session_command(session_id, request, **kwargs), self._owner, self._guard_loop
        )

    def delete_session(self, session_id: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_session(session_id, **kwargs), self._owner, self._guard_loop)


class _SyncFileSystem:
    def __init__(
        self,
        service: Any,
        owner: _SyncBridgeLoop,
        guard_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._service = service
        self._owner = owner
        self._guard_loop = guard_loop

    def upload_file(self, content: bytes, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.upload_file(content, path, **kwargs), self._owner, self._guard_loop)

    def download_file(self, path: str, **kwargs: Any) -> bytes:
        return _sync_await(self._service.download_file(path, **kwargs), self._owner, self._guard_loop)

    def delete_file(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.delete_file(path, **kwargs), self._owner, self._guard_loop)

    def list_files(self, path: str, **kwargs: Any) -> Any:
        return _sync_await(self._service.list_files(path, **kwargs), self._owner, self._guard_loop)


class _DSPySyncSandboxView:
    """Explicit synchronous Daytona view used only by DSPy worker execution.

    Routes SDK coroutines through the composition-wide bridge service loop
    exposed by the injected dispatcher; the ``loop`` constructor argument
    anchors the fail-fast owning-loop guard and is the fallback target when
    no dispatcher is injected.
    """

    def __init__(
        self,
        sandbox: Any,
        loop: asyncio.AbstractEventLoop,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        owner = _SyncBridgeLoop(caller_loop=loop, dispatcher=dispatcher)
        if hasattr(sandbox, "code_interpreter"):
            self.code_interpreter = _SyncCodeInterpreter(sandbox.code_interpreter, owner, loop)
        if hasattr(sandbox, "process"):
            self.process = _SyncProcess(sandbox.process, owner, loop)
        if hasattr(sandbox, "fs"):
            self.fs = _SyncFileSystem(sandbox.fs, owner, loop)
        self._sandbox = sandbox
        self._loop = loop
        self._owner = owner

    @property
    def id(self) -> object:
        """Expose the wrapped provider identity to per-Sandbox host caches."""
        return getattr(self._sandbox, "id", None)

    def get_preview_link(self, port: int, **kwargs: Any) -> Any:
        return _sync_await(self._sandbox.get_preview_link(port, **kwargs), self._owner, self._loop)

    def close(self) -> None:
        """Tombstone the bridge; further calls fail fast until start()."""
        self._owner.close()

    def start(self) -> None:
        """Clear the close tombstone after close()."""
        self._owner.start()


def sync_sandbox(
    sandbox: Any,
    loop: asyncio.AbstractEventLoop,
    dispatcher: SyncBridgeDispatcher | None = None,
) -> Any:
    """Return a synchronous sandbox view for DSPy worker-thread execution.

    ``dispatcher`` injects the composition-owned bridge authority (QRE-154);
    when omitted, the view falls back to its caller-captured loop. The
    concrete view type is private to this module. Callers that
    need to invalidate a view after lease release should use
    :func:`tombstone_sync_sandbox`.
    """
    if isinstance(sandbox, _DSPySyncSandboxView):
        return sandbox
    return _DSPySyncSandboxView(sandbox, loop, dispatcher)


def tombstone_sync_sandbox(sandbox: Any) -> None:
    """Tombstone a sync sandbox view so late calls fail typed-fast.

    No-op when ``sandbox`` is not a view created by :func:`sync_sandbox`.
    The shared bridge service loop outlives individual Turns.
    """
    if isinstance(sandbox, _DSPySyncSandboxView):
        sandbox.close()


__all__ = [
    "BROKER_SERVER_CODE",
    "DEFAULT_BROKER_PORT",
    "FINAL_OUTPUT_MARKER",
    "TOOL_WRAPPER_TEMPLATE",
    "DaytonaHttpToolBroker",
    "FleetFinalOutputError",
    "SyncBridgeDispatcher",
    "_DSPySyncSandboxView",
    "_SyncBridgeLoop",
    "_SyncCodeInterpreter",
    "_SyncFileSystem",
    "_SyncProcess",
    "_sync_await",
    "build_submit_setup_code",
    "extract_final_payload",
    "final_output_frame",
    "remote_submit_setup_code",
    "reset_binding_source",
    "sync_sandbox",
    "tombstone_sync_sandbox",
]
