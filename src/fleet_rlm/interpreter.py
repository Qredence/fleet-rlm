"""Session-scoped Daytona sandbox and DSPy interpreter integration."""

from __future__ import annotations

import base64
import contextvars
import json
import keyword
import logging
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import dspy
from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig, VolumeMount
from dspy.signatures.signature import make_signature

from fleet_rlm.dspy_support import make_choice_type
from fleet_rlm.skills import SANDBOX_SKILL_HELPERS_CODE

_LOGGER = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NAMESPACE_RE = re.compile(r"^ns_[a-f0-9]{8}$")
_ALLOWED_WORKSPACE_ROOTS = frozenset({"files", "artifacts", "memory", "skills"})


def validate_session_id(session_id: str) -> str:
    """Validate a session key before it is used as a Daytona Volume subpath."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1-64 letters, digits, dots, underscores, or hyphens")
    if session_id in {".", ".."} or ".." in session_id:
        raise ValueError("session_id must not contain parent path syntax")
    return session_id


def session_volume_subpath(session_id: str) -> str:
    """Return the provider-enforced private subtree for one Session."""
    return f"sessions/{validate_session_id(session_id)}"


def _workspace_relative_path(relative_path: str) -> str:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 1024
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(ord(character) < 32 for character in relative_path)
    ):
        raise ValueError("Workspace path must be a non-empty POSIX-relative path")
    parts = relative_path.split("/")
    if parts[0] not in _ALLOWED_WORKSPACE_ROOTS or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path is outside the allowed files, artifacts, memory, and skills roots")
    return "/".join(parts)


_DAEMON_CODE = r"""
import ast, builtins, hmac, io, json, keyword, os, re, sys, threading, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAMESPACES = {}
NS_LOCKS = {}
NAMESPACE_GUARD = threading.Lock()
PENDING_CALLS = {}
IN_FLIGHT_CALLS = set()
RESULTS = {}
CALL_NAMESPACES = {}
POLL_CONDITION = threading.Condition()
MAX_BODY = 2_000_000
MAX_CODE = 1_000_000
MAX_PENDING_CALLS = 64
MAX_CAPTURE_CHARS = 100_000
TOKEN = __FLEET_TOKEN__
OUTPUT_CONTEXT = threading.local()

class BoundedBuffer(io.StringIO):
    def __init__(self):
        super().__init__()
        self.truncated = False

    def write(self, value):
        remaining = MAX_CAPTURE_CHARS - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(value)
        written = super().write(value[:remaining])
        if len(value) > written:
            self.truncated = True
        return len(value)

class ThreadOutput:
    def __init__(self, channel, fallback):
        self.channel = channel
        self.fallback = fallback

    def write(self, value):
        return getattr(OUTPUT_CONTEXT, self.channel, self.fallback).write(value)

    def flush(self):
        return getattr(OUTPUT_CONTEXT, self.channel, self.fallback).flush()

    def isatty(self):
        return False

_DEFAULT_STDOUT = sys.stdout
_DEFAULT_STDERR = sys.stderr
sys.stdout = ThreadOutput("stdout", _DEFAULT_STDOUT)
sys.stderr = ThreadOutput("stderr", _DEFAULT_STDERR)

class FinalOutput(BaseException):
    def __init__(self, payload):
        self.payload = payload

def _jsonable(value):
    try:
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError):
        return repr(value)

def _bridge_call(ns_id, tool_name, args, kwargs):
    encoded = json.dumps({"tool": tool_name, "args": args, "kwargs": kwargs}, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_BODY:
        raise ValueError("Host tool request exceeds the size limit")
    call_id = os.urandom(12).hex()
    with POLL_CONDITION:
        if len(PENDING_CALLS) + len(IN_FLIGHT_CALLS) >= MAX_PENDING_CALLS:
            raise RuntimeError("Too many pending host tool calls")
        PENDING_CALLS[call_id] = {"id": call_id, "ns_id": ns_id, "tool": tool_name, "args": args, "kwargs": kwargs}
        CALL_NAMESPACES[call_id] = ns_id
        POLL_CONDITION.notify_all()
    with POLL_CONDITION:
        if not POLL_CONDITION.wait_for(lambda: call_id in RESULTS, timeout=120.0):
            PENDING_CALLS.pop(call_id, None)
            IN_FLIGHT_CALLS.discard(call_id)
            RESULTS.pop(call_id, None)
            CALL_NAMESPACES.pop(call_id, None)
            raise TimeoutError("Host tool call timed out")
        result = RESULTS.pop(call_id)
        CALL_NAMESPACES.pop(call_id, None)
    if result.get("error"):
        raise RuntimeError(str(result["error"])[:2000])
    return result.get("value")

def _make_tool(ns_id, tool_name):
    def tool(*args, **kwargs):
        return _bridge_call(ns_id, tool_name, list(args), kwargs)

    tool.__name__ = tool_name
    return tool

def _make_namespace(ns_id, tool_names):
    namespace = {
        "__builtins__": builtins,
        "WORKSPACE": "/workspace",
        "llm_query": lambda prompt: _bridge_call(ns_id, "llm_query", [prompt], {}),
        "llm_query_batched": lambda prompts: _bridge_call(ns_id, "llm_query_batched", [list(prompts)], {}),
        "jev_decide_batched": lambda items, question, choices: _bridge_call(
            ns_id, "jev_decide_batched", [list(items), question, list(choices)], {}
        ),
        "list_skills": list_skills,
        "use_skill": use_skill,
        "read_skill_reference": read_skill_reference,
        "run_skill_script": run_skill_script,
        "save_skill": save_skill,
        "SUBMIT": lambda **kwargs: (_ for _ in ()).throw(FinalOutput(kwargs)),
    }
    for name in tool_names:
        if isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name) and name not in namespace:
            namespace[name] = _make_tool(ns_id, name)
    return namespace

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        return

    def _authorized(self):
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, "Bearer " + TOKEN)

    def _send(self, body, status=200):
        try:
            raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            status = 500
            raw = b'{"error":"invalid response"}'
        if len(raw) > MAX_BODY:
            status = 413
            raw = b'{"error":"response too large"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self._authorized():
            return self._send({"error": "unauthorized"}, 401)
        if self.path == "/health":
            return self._send({"ok": True})
        if self.path == "/poll":
            with POLL_CONDITION:
                if not PENDING_CALLS:
                    POLL_CONDITION.wait(timeout=0.25)
                calls = list(PENDING_CALLS.values())
                PENDING_CALLS.clear()
                IN_FLIGHT_CALLS.update(call["id"] for call in calls)
            return self._send({"calls": calls})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            return self._send({"error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send({"error": "invalid content length"}, 400)
        if length < 0 or length > MAX_BODY:
            return self._send({"error": "request too large"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send({"error": "invalid JSON"}, 400)
        if not isinstance(body, dict):
            return self._send({"error": "request must be an object"}, 400)
        if self.path == "/reply":
            call_id = body.get("id")
            with POLL_CONDITION:
                if not isinstance(call_id, str) or call_id not in IN_FLIGHT_CALLS:
                    return self._send({"error": "unknown call"}, 404)
                IN_FLIGHT_CALLS.remove(call_id)
                CALL_NAMESPACES.pop(call_id, None)
                RESULTS[call_id] = body
                POLL_CONDITION.notify_all()
            return self._send({"ok": True})
        if self.path == "/drop_ns":
            ns_id = body.get("ns_id")
            if not isinstance(ns_id, str) or not re.fullmatch(r"ns_[a-f0-9]{8}", ns_id):
                return self._send({"error": "invalid namespace"}, 400)
            with NAMESPACE_GUARD:
                NAMESPACES.pop(ns_id, None)
                NS_LOCKS.pop(ns_id, None)
            with POLL_CONDITION:
                for call_id, call in list(PENDING_CALLS.items()):
                    if call["ns_id"] == ns_id:
                        PENDING_CALLS.pop(call_id, None)
                        CALL_NAMESPACES.pop(call_id, None)
                        RESULTS[call_id] = {"error": "Interpreter namespace was closed"}
                for call_id in list(IN_FLIGHT_CALLS):
                    if CALL_NAMESPACES.get(call_id) == ns_id:
                        IN_FLIGHT_CALLS.discard(call_id)
                        CALL_NAMESPACES.pop(call_id, None)
                        RESULTS[call_id] = {"error": "Interpreter namespace was closed"}
                POLL_CONDITION.notify_all()
            return self._send({"ok": True})
        if self.path == "/exec":
            ns_id, code = body.get("ns_id"), body.get("code")
            if not isinstance(ns_id, str) or not re.fullmatch(r"ns_[a-f0-9]{8}", ns_id):
                return self._send({"error": "invalid namespace"}, 400)
            if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE:
                return self._send({"error": "invalid or oversized code"}, 413)
            variables = body.get("variables") or {}
            tools = body.get("tools") or []
            reserved_names = {
                "__builtins__", "llm_query", "llm_query_batched", "jev_decide_batched", "SUBMIT",
                "list_skills", "use_skill", "read_skill_reference", "run_skill_script", "save_skill",
            }
            # DSPy Flex lists these existing interpreter APIs as registered tools.
            registered_builtin_tools = {"llm_query", "llm_query_batched", "jev_decide_batched"}
            reserved_tool_names = reserved_names - registered_builtin_tools
            if (
                not isinstance(variables, dict)
                or len(variables) > 1000
                or not isinstance(tools, list)
                or len(tools) > 64
                or any(
                    not isinstance(name, str)
                    or len(name) > 128
                    or not name.isidentifier()
                    or keyword.iskeyword(name)
                    or name in reserved_tool_names
                    for name in tools
                )
            ):
                return self._send({"error": "invalid execution bindings"}, 400)
            with NAMESPACE_GUARD:
                namespace = NAMESPACES.setdefault(ns_id, _make_namespace(ns_id, tools))
                execution_lock = NS_LOCKS.setdefault(ns_id, threading.Lock())
            protected = reserved_names | set(tools)
            if any(not isinstance(key, str) for key in variables):
                return self._send({"error": "execution variable names must be strings"}, 400)
            output, errors = BoundedBuffer(), BoundedBuffer()
            with execution_lock:
                namespace.update({
                    key: value
                    for key, value in variables.items()
                    if key.isidentifier() and not keyword.iskeyword(key) and key not in protected
                })
                try:
                    OUTPUT_CONTEXT.stdout = output
                    OUTPUT_CONTEXT.stderr = errors
                    tree = ast.parse(code, mode="exec")
                    last_statement = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                    exec(compile(tree, "<fleet_rlm>", "exec"), namespace, namespace)
                    value = (
                        eval(compile(ast.Expression(last_statement.value), "<fleet_rlm>", "eval"), namespace)
                        if last_statement
                        else None
                    )
                    result = {
                        "stdout": output.getvalue(),
                        "stderr": errors.getvalue(),
                        "final": None,
                        "has_value": last_statement is not None,
                        "value": _jsonable(value),
                    }
                except FinalOutput as final:
                    try:
                        final_json = json.dumps(final.payload, separators=(",", ":"))
                        if len(final_json.encode("utf-8")) > MAX_BODY:
                            raise ValueError("final output exceeds the size limit")
                        result = {"stdout": output.getvalue(), "stderr": errors.getvalue(), "final": final.payload}
                    except (TypeError, ValueError) as exc:
                        errors.write(str(exc))
                        result = {"stdout": output.getvalue(), "stderr": errors.getvalue(), "error": True}
                except Exception:
                    errors.write(traceback.format_exc())
                    result = {"stdout": output.getvalue(), "stderr": errors.getvalue(), "error": True}
                finally:
                    del OUTPUT_CONTEXT.stdout
                    del OUTPUT_CONTEXT.stderr
                if output.truncated or errors.truncated:
                    result["truncated"] = True
            return self._send(result)
        return self._send({"error": "not found"}, 404)

for directory in ("files", "artifacts", "memory", "skills"):
    os.makedirs(os.path.join("/workspace", directory), exist_ok=True)
sys.path.insert(0, "/workspace")
ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
"""


@dataclass(slots=True)
class DaytonaSandboxManager:
    """Own one warm Daytona Sandbox mounted to a single Session's Volume subpath."""

    api_key: str
    org_id: str
    volume_id: str
    session_id: str
    snapshot: str = "fleet-rlm-base"
    auto_stop_minutes: int = 30
    sub_lm: dspy.LM | None = None
    jev_lm: Any | None = None
    max_llm_calls: int = 150
    _sandbox: Any = field(default=None, init=False, repr=False)
    _client: Daytona | None = field(default=None, init=False, repr=False)
    _token: str = field(default_factory=lambda: secrets.token_urlsafe(32), init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _budget_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _llm_calls_used: int = field(default=0, init=False, repr=False)
    _interpreters: dict[str, DaytonaInterpreter] = field(default_factory=dict, init=False, repr=False)
    _poller: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop_poller: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _daemon_ready: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        validate_session_id(self.session_id)
        if not self.api_key or not self.org_id or not self.volume_id:
            raise ValueError("Daytona API key, organization ID, and Volume ID are required")
        if self.max_llm_calls < 1:
            raise ValueError("max_llm_calls must be positive")

    def ensure_sandbox(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = Daytona(DaytonaConfig(api_key=self.api_key, organization_id=self.org_id))
            if self._sandbox is None:
                params = CreateSandboxFromSnapshotParams(
                    snapshot=self.snapshot,
                    language="python",
                    volumes=[
                        VolumeMount(
                            volume_id=self.volume_id,
                            mount_path="/workspace",
                            subpath=session_volume_subpath(self.session_id),
                        )
                    ],
                    auto_stop_interval=self.auto_stop_minutes,
                )
                self._sandbox = self._client.create(params)
            state = getattr(self._sandbox, "state", None)
            normalized_state = str(getattr(state, "value", state) or "").lower()
            if normalized_state in {"stopped", "archived", "archiving", "stopping"}:
                self._client.start(self._sandbox)
                self._daemon_ready = False
            if not self._daemon_ready:
                self._ensure_daemon(self._sandbox)
            return self._sandbox

    def _remote_exec(self, sandbox: Any, code: str) -> str:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = (
            "python3 -c 'import base64; exec(compile(base64.b64decode(\"" + encoded + '"), "<fleet-remote>", "exec"))\''
        )
        result = sandbox.process.exec(command)
        if getattr(result, "exit_code", 0) != 0:
            raise RuntimeError("Remote Daytona command failed")
        return getattr(result, "result", "") or ""

    def _ensure_daemon(self, sandbox: Any) -> None:
        token_b64 = base64.b64encode(self._token.encode("utf-8")).decode("ascii")
        health_code = (
            "import base64,json,urllib.request\n"
            "authorization='Bearer '+base64.b64decode('" + token_b64 + "').decode()\n"
            "r=urllib.request.Request('http://127.0.0.1:8765/health',headers={'Authorization':authorization})\n"
            "print(urllib.request.urlopen(r,timeout=2).read().decode())"
        )
        try:
            if json.loads(self._remote_exec(sandbox, health_code)).get("ok") is True:
                self._daemon_ready = True
                return
        except Exception:
            pass

        token_source = "TOKEN = " + repr(self._token) + "\n"
        daemon_code = (
            SANDBOX_SKILL_HELPERS_CODE + "\n" + token_source + _DAEMON_CODE.replace("TOKEN = __FLEET_TOKEN__\n", "")
        )
        daemon_b64 = base64.b64encode(daemon_code.encode("utf-8")).decode("ascii")
        start_code = (
            "import base64,pathlib\n"
            "pathlib.Path('/tmp/fleet_rlm_daemon.py').write_bytes(base64.b64decode('" + daemon_b64 + "'))"
        )
        self._remote_exec(sandbox, start_code)
        sandbox.process.exec("nohup python3 /tmp/fleet_rlm_daemon.py >/tmp/fleet_rlm_daemon.log 2>&1 </dev/null &")
        for _ in range(40):
            try:
                if json.loads(self._remote_exec(sandbox, health_code)).get("ok") is True:
                    self._daemon_ready = True
                    return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("Daytona interpreter daemon failed to become ready")

    def rpc(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in {"/health", "/poll", "/reply", "/drop_ns", "/exec"}:
            raise ValueError("Unsupported Daytona interpreter RPC")
        if payload is not None:
            if not isinstance(payload, dict):
                raise TypeError("Daytona interpreter RPC payload must be an object")
            if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > 1_500_000:
                raise ValueError("Daytona interpreter RPC payload exceeds the size limit")
        sandbox = self.ensure_sandbox()
        token_b64 = base64.b64encode(self._token.encode("utf-8")).decode("ascii")
        if payload is None:
            request_code = (
                "import base64,json,urllib.request\n"
                f"authorization='Bearer '+base64.b64decode('{token_b64}').decode()\n"
                f"r=urllib.request.Request('http://127.0.0.1:8765{path}',headers={{'Authorization':authorization}})\n"
            )
        else:
            encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
            request_code = (
                "import base64,json,urllib.request\n"
                f"body=base64.b64decode('{encoded}')\n"
                f"authorization='Bearer '+base64.b64decode('{token_b64}').decode()\n"
                f"headers={{'Authorization':authorization,'Content-Type':'application/json'}}\n"
                f"r=urllib.request.Request('http://127.0.0.1:8765{path}',data=body,headers=headers)\n"
            )
        request_code += "print(urllib.request.urlopen(r,timeout=180).read().decode())"
        try:
            response = self._remote_exec(sandbox, request_code)
        except Exception:
            with self._lock:
                self._daemon_ready = False
            raise
        result = json.loads(response)
        if not isinstance(result, dict):
            raise RuntimeError("Invalid response from Daytona interpreter daemon")
        return result

    def exec_remote_python(self, code: str) -> str:
        """Run bounded maintenance Python in this Session's sandbox."""
        if len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Remote maintenance code exceeds the size limit")
        return self._remote_exec(self.ensure_sandbox(), code)

    def reset_turn_budget(self) -> None:
        with self._budget_lock:
            self._llm_calls_used = 0

    def consume_budget(self, count: int = 1) -> None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("budget consumption must be a positive integer")
        with self._budget_lock:
            if self._llm_calls_used + count > self.max_llm_calls:
                raise RuntimeError(f"Shared RLM budget exhausted ({self.max_llm_calls} calls)")
            self._llm_calls_used += count

    def list_workspace_files(self) -> list[str]:
        code = (
            "import json,os\n"
            "root='/workspace'\n"
            "items=[]\n"
            "for base,dirs,files in os.walk(root,followlinks=False):\n"
            " dirs[:]=[name for name in dirs if not os.path.islink(os.path.join(base,name))]\n"
            " for name in files:\n"
            "  path=os.path.join(base,name)\n"
            "  if not os.path.islink(path) and os.path.isfile(path): items.append(os.path.relpath(path,root))\n"
            "print(json.dumps(sorted(items)))"
        )
        return json.loads(self.exec_remote_python(code) or "[]")

    def write_workspace_file(self, rel_path: str, content: bytes) -> str:
        """Write one validated file beneath this Session's mounted Volume."""
        clean_path = _workspace_relative_path(rel_path)
        if not isinstance(content, bytes):
            raise TypeError("workspace content must be bytes")
        if len(content) > 25_000_000:
            raise ValueError("Workspace file exceeds the 25 MB limit")
        sandbox = self.ensure_sandbox()
        self._workspace_file_metadata(sandbox, clean_path, create_parents=True)
        sandbox.fs.upload_file(content, f"/workspace/{clean_path}", timeout=1800)
        return f"/workspace/{clean_path}"

    def read_workspace_file(self, rel_path: str, *, max_bytes: int = 25_000_000) -> bytes:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        content = sandbox.fs.download_file(f"/workspace/{clean_path}", 1800)
        if not isinstance(content, bytes) or len(content) != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return content

    def _workspace_file_metadata(
        self,
        sandbox: Any,
        rel_path: str,
        *,
        create_parents: bool,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Check a Volume path remotely before using Daytona's file-transfer API."""
        path_b64 = base64.b64encode(rel_path.encode("utf-8")).decode("ascii")
        create_clause = "os.makedirs(parent,exist_ok=True)\n" if create_parents else ""
        read_clause = "if not os.path.isfile(target): raise FileNotFoundError('workspace file is unavailable')\n"
        if create_parents:
            read_clause = (
                "if os.path.exists(target) and not os.path.isfile(target): "
                "raise ValueError('workspace target is not a file')\n"
            )
        size_clause = "size=os.path.getsize(target) if os.path.isfile(target) else None\n"
        if max_bytes is not None:
            size_clause += f"if size>{max_bytes}: raise ValueError('workspace file exceeds size limit')\n"
        code = (
            "import base64,json,os\n"
            "root=os.path.realpath('/workspace')\n"
            "relative=base64.b64decode('" + path_b64 + "').decode('utf-8')\n"
            "parts=relative.split('/')\n"
            "parent=root\n"
            "for part in parts[:-1]:\n"
            " parent=os.path.join(parent,part)\n"
            " if os.path.islink(parent): raise ValueError('workspace path may not traverse symbolic links')\n"
            " if os.path.exists(parent) and not os.path.isdir(parent): "
            "raise ValueError('workspace parent is not a directory')\n"
            "if os.path.commonpath((root,os.path.realpath(parent))) != root: "
            "raise ValueError('invalid workspace path')\n" + create_clause + "target=os.path.join(parent,parts[-1])\n"
            "if os.path.islink(target): raise ValueError('workspace target may not be a symbolic link')\n"
            "if os.path.commonpath((root,parent)) != root: raise ValueError('invalid workspace path')\n"
            + read_clause
            + size_clause
            + "print(json.dumps({'size':size}))"
        )
        metadata = json.loads(self._remote_exec(sandbox, code))
        if not isinstance(metadata, dict) or (
            metadata.get("size") is not None and not isinstance(metadata["size"], int)
        ):
            raise RuntimeError("Invalid workspace file metadata")
        return metadata

    def interpreter_factory(
        self, tools: Mapping[str, Callable[..., Any]] | None = None
    ) -> Callable[[], DaytonaInterpreter]:
        """Return the zero-argument fresh interpreter factory required by DSPy 3.4."""
        return lambda: DaytonaInterpreter(manager=self, tools=dict(tools or {}))

    def _register_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters[interpreter.ns_id] = interpreter
            if self._poller is None or not self._poller.is_alive():
                self._stop_poller.clear()
                self._poller = threading.Thread(target=self._poll_loop, name="fleet-daytona-poller", daemon=True)
                self._poller.start()

    def _unregister_interpreter(self, interpreter: DaytonaInterpreter) -> None:
        with self._lock:
            self._interpreters.pop(interpreter.ns_id, None)

    def _poll_loop(self) -> None:
        while not self._stop_poller.is_set():
            try:
                calls = self.rpc("/poll").get("calls", [])
                for call in calls:
                    with self._lock:
                        interpreter = self._interpreters.get(str(call.get("ns_id")))
                    if interpreter is None:
                        self.rpc("/reply", {"id": call.get("id"), "error": "Interpreter is no longer active"})
                        continue
                    context = interpreter._context.copy()
                    threading.Thread(
                        target=context.run,
                        args=(interpreter._handle_host_call, call),
                        name="fleet-daytona-tool",
                        daemon=True,
                    ).start()
            except Exception:
                if not self._stop_poller.is_set():
                    time.sleep(0.2)

    def shutdown(self) -> None:
        self._stop_poller.set()
        poller = self._poller
        if poller is not None and poller is not threading.current_thread():
            poller.join(timeout=2)
        with self._lock:
            sandbox, client = self._sandbox, self._client
            interpreters = tuple(self._interpreters.values())
            self._sandbox = None
            self._daemon_ready = False
            self._interpreters.clear()
            for interpreter in interpreters:
                interpreter._closed = True
        if sandbox is not None and client is not None:
            try:
                client.stop(sandbox, timeout=60)
            except Exception:
                _LOGGER.warning("Daytona Session sandbox stop failed")


class DaytonaInterpreter:
    """DSPy CodeInterpreter backed by a namespace in a Session-isolated sandbox."""

    def __init__(self, manager: DaytonaSandboxManager, tools: dict[str, Callable[..., Any]] | None = None):
        self.manager = manager
        self.tools = dict(tools or {})
        reserved = {
            "__builtins__",
            "llm_query",
            "llm_query_batched",
            "jev_decide_batched",
            "SUBMIT",
            "list_skills",
            "use_skill",
            "read_skill_reference",
            "run_skill_script",
            "save_skill",
        }
        if any(
            not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name in reserved
            for name in self.tools
        ):
            raise ValueError("Bridged tool names must be unreserved Python identifiers")
        self.ns_id = f"ns_{uuid.uuid4().hex[:8]}"
        self._context = contextvars.copy_context()
        self._started = False
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Interpreter has already been shut down")
        self.manager.ensure_sandbox()
        if not self._started:
            self.manager._register_interpreter(self)
            self._started = True

    def _handle_host_call(self, call: dict[str, Any]) -> None:
        call_id = call.get("id")
        tool_name = call.get("tool")
        args = call.get("args", [])
        kwargs = call.get("kwargs", {})
        try:
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("Invalid host tool arguments")
            if tool_name == "llm_query":
                if len(args) != 1 or not isinstance(args[0], str) or len(args[0]) > 100_000:
                    raise ValueError("llm_query requires one prompt of at most 100000 characters")
                self.manager.consume_budget()
                active_lm = self.manager.sub_lm or dspy.settings.lm
                with dspy.context(lm=active_lm, disable_history=True):
                    value = active_lm(args[0])[0]
            elif tool_name == "llm_query_batched":
                if len(args) != 1:
                    raise ValueError("llm_query_batched requires one prompt list")
                prompts = args[0]
                if (
                    not isinstance(prompts, list)
                    or not prompts
                    or len(prompts) > 100
                    or any(not isinstance(prompt, str) or len(prompt) > 100_000 for prompt in prompts)
                ):
                    raise ValueError("llm_query_batched requires 1-100 bounded string prompts")
                self.manager.consume_budget(len(prompts))
                active_lm = self.manager.sub_lm or dspy.settings.lm

                def complete(prompt: str) -> Any:
                    with dspy.context(lm=active_lm, disable_history=True):
                        return active_lm(prompt)[0]

                with ThreadPoolExecutor(max_workers=min(8, len(prompts))) as pool:
                    value = list(pool.map(complete, prompts))
            elif tool_name == "jev_decide_batched":
                if len(args) != 3:
                    raise ValueError("jev_decide_batched requires items, question, and choices")
                items, question, choices = args
                if (
                    not isinstance(items, list)
                    or any(not isinstance(item, str) or len(item) > 100_000 for item in items)
                    or not isinstance(question, str)
                    or len(question) > 10_000
                    or not isinstance(choices, list)
                    or any(not isinstance(choice, str) or not choice or len(choice) > 256 for choice in choices)
                    or len(set(choices)) != len(choices)
                ):
                    raise ValueError("Invalid Jev batch inputs")
                self.manager.consume_budget(len(items))
                value = self._run_jev_batch(items, question, choices)
            elif tool_name in self.tools:
                value = self.tools[tool_name](*args, **kwargs)
            else:
                raise KeyError(f"Unknown bridged tool: {tool_name}")
            self.manager.rpc("/reply", {"id": call_id, "value": value})
        except Exception as exc:
            try:
                self.manager.rpc("/reply", {"id": call_id, "error": str(exc)[:2000]})
            except Exception:
                _LOGGER.warning("Could not return a host tool result to a Daytona Session sandbox")

    def _run_jev_batch(self, items: list[str], question: str, choices: list[str]) -> list[dict[str, Any]]:
        if not items or not choices or len(items) > 100 or len(choices) > 20:
            raise ValueError("Jev batch inputs exceed the supported bounds")
        from pydantic import BaseModel

        choice_type = make_choice_type(tuple((choice, choice) for choice in choices))
        item_decision_signature = make_signature(
            "item: str, question: str -> decision: FleetChoice",
            custom_types={"FleetChoice": choice_type},
        )

        predictor = dspy.Predict(item_decision_signature)
        active_lm = self.manager.jev_lm or self.manager.sub_lm or dspy.settings.lm
        examples = [dspy.Example(item=item, question=question).with_inputs("item", "question") for item in items]
        with dspy.context(lm=active_lm, disable_history=True):
            predictions = predictor.batch(examples, num_threads=min(8, len(items)))
        results: list[dict[str, Any]] = []
        for item, prediction in zip(items, predictions, strict=True):
            decision = getattr(prediction, "decision", None)
            choice_value = getattr(decision, "value", None) if isinstance(decision, BaseModel) else None
            if not isinstance(choice_value, str):
                raise RuntimeError("Jev returned an invalid decision")
            results.append({"item": item, "decision": choice_value})
        return results

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if not isinstance(code, str) or len(code.encode("utf-8")) > 1_000_000:
            raise ValueError("Interpreter code exceeds the 1 MB size limit")
        if variables is not None and not isinstance(variables, dict):
            raise TypeError("Interpreter variables must be an object")
        self.start()
        response = self.manager.rpc(
            "/exec",
            {"ns_id": self.ns_id, "code": code, "variables": variables or {}, "tools": list(self.tools)},
        )
        from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput

        if response.get("final") is not None:
            return FinalOutput(response["final"])
        output = (response.get("stdout") or "") + (response.get("stderr") or "")
        if response.get("error"):
            message = output.strip() or "Remote Python execution failed"
            raise CodeExecutionError(message[-20_000:])
        if response.get("has_value") and response.get("value") is not None:
            return response["value"]
        return output[-20_000:].strip() or None

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            try:
                self.manager.rpc("/drop_ns", {"ns_id": self.ns_id})
            except Exception:
                _LOGGER.debug("Daytona interpreter namespace cleanup did not complete")
            finally:
                self.manager._unregister_interpreter(self)
