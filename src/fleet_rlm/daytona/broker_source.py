"""Pure source generation for the Daytona host-tool broker."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

FINAL_OUTPUT_MARKER = "__FLEET_FINAL_OUTPUT__"
_FINAL_OUTPUT_MARKER = FINAL_OUTPUT_MARKER


def build_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    return _typed_submit_source(output_fields) if output_fields else _generic_submit_source()


def remote_submit_setup_code(output_fields: list[dict[str, Any]] | None) -> str:
    return f"""
import base64 as _base64
import json
_json = json
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
    validation_parts: list[str] = []
    result_parts: list[str] = []
    for field in output_fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        type_hint = str(field.get("type") or "").strip()
        signature_parts.append(f"{name}: {type_hint}" if type_hint else name)
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
        result_parts.append(f'"{name}": {name}')
    signature = ", ".join(signature_parts) or "**kwargs"
    body_lines = [
        *validation_parts,
        f"result = {{{', '.join(result_parts)}}}" if result_parts else "result = dict(kwargs)",
    ]
    body = "\n    ".join(body_lines)
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


BROKER_SERVER_CODE = """
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


__all__ = [
    "BROKER_SERVER_CODE",
    "FINAL_OUTPUT_MARKER",
    "TOOL_WRAPPER_TEMPLATE",
    "build_submit_setup_code",
    "extract_final_payload",
    "final_output_frame",
    "remote_submit_setup_code",
]
