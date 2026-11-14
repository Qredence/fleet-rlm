"""Runs inside one Daytona interpreter context: a Fleet namespace, bounded output, and the host-tool bridge.

The host loads this file once per context, after the Skill and workspace helpers, then runs every
execution as ``_fleet_exec(<payload json>)``. Daytona sends each stdout ``write()`` as its own websocket
message and drops the connection on bursts of about 2,000 writes per second, so user output is
captured and returned in a single ``FLEET_RESULT`` line. Host calls are single locked ``FLEET_CALL``
lines; the host answers by uploading ``REPLY_DIR/<call id>.json``.

This file must stay standard-library only: it runs in the sandbox image, not in Fleet's environment,
and it is concatenated after other helper code, so it must not use ``from __future__`` imports.
"""

import ast
import builtins
import contextlib
import ctypes
import functools
import io
import json
import keyword
import math
import os
import signal
import sys
import threading
import time
import traceback
import types
import uuid
from typing import Any

CALL_MARK = "\x1eFLEET_CALL "
RESULT_MARK = "\x1eFLEET_RESULT "
REPLY_DIR = "/tmp/fleet/replies"
WORKSPACE = "/workspace"
MAX_BODY = 2_000_000
MAX_CODE = 1_000_000
MAX_CAPTURE_CHARS = 100_000
RESERVED_NAMES = {
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
# DSPy RLM and Flex register these built-in interpreter APIs in payload["tools"].
REGISTERED_BUILTIN_TOOLS = {"llm_query", "llm_query_batched", "jev_decide_batched"}
SKILL_HELPERS = ("list_skills", "use_skill", "read_skill_reference", "run_skill_script", "save_skill")
MAX_SKILL_RECEIPTS = 20
DEFAULT_EXEC_TIMEOUT_SECONDS = 60.0

_STATE: dict[str, Any] = {
    "module": None,
    "namespace": None,
    "registered_tools": set(),
    "stdout": None,
    "host_call_timeout": None,
    "exec_timeout": None,
    "exec_timer": None,
}
_WRITE_LOCK = threading.Lock()


class BoundedBuffer(io.StringIO):
    """StringIO that keeps at most MAX_CAPTURE_CHARS characters and records whether output was dropped."""

    def __init__(self) -> None:
        """Start an empty capture buffer with no truncation recorded."""
        super().__init__()
        self.truncated = False

    def write(self, value: str) -> int:
        """Write up to the capture limit, recording when output is dropped."""
        remaining = MAX_CAPTURE_CHARS - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(value)
        written = super().write(value[:remaining])
        if len(value) > written:
            self.truncated = True
        return len(value)


class FinalOutput(BaseException):
    """Raised by SUBMIT to end an execution with its final payload."""

    def __init__(self, payload: Any) -> None:
        """Store the payload that SUBMIT produced."""
        self.payload = payload


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _emit(mark: str, payload: dict[str, Any]) -> None:
    """One locked write to the run's real stdout, so concurrent calls never interleave."""
    line = mark + json.dumps(payload, separators=(",", ":")) + "\n"
    with _WRITE_LOCK:
        stream: Any = _STATE["stdout"] or sys.__stdout__
        stream.write(line)
        stream.flush()


def _host_call_limit(value: Any) -> float | None:
    """Return a positive host-call wait in seconds as a float.

    None and nonpositive values mean no bound; no fallback timeout is substituted.
    """
    if value is None or value <= 0:
        return None
    return float(value)


def _exec_limit() -> float | None:
    """Return a bounded positive per-cell local timeout, or None when host timeout owns it."""
    raw = _STATE.get("exec_timeout")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw > 0:
        return float(raw)
    host_limit = _host_call_limit(_STATE.get("host_call_timeout"))
    if host_limit is not None and host_limit <= DEFAULT_EXEC_TIMEOUT_SECONDS:
        return None
    return DEFAULT_EXEC_TIMEOUT_SECONDS


class _LocalExecTimer:
    """Bound local time per cell, excluding intervals with any pending host call.

    Calls from all threads share the pause; local work concurrent with a host wait is
    also excluded. Only the last completed call resumes the remaining budget.
    """

    def __init__(self, limit_seconds: float) -> None:
        self.limit = limit_seconds
        self.remaining = limit_seconds
        self._lock = threading.Lock()
        self._tid = threading.get_ident()
        self._use_signal = (
            threading.current_thread() is threading.main_thread()
            and hasattr(signal, "SIGALRM")
            and hasattr(signal, "setitimer")
        )
        self._armed = False
        self._closed = False
        self._host_waits = 0
        self._generation = 0
        self._started_at = 0.0
        self._timed_out = False
        self._timer: threading.Timer | None = None
        self._prev_handler: Any = None
        self._prev_timer: tuple[float, float] = (0.0, 0.0)

    def _timeout_error(self) -> TimeoutError:
        return TimeoutError(f"Sandbox code execution exceeded {self.limit:g}s local limit")

    def _handle_alarm(self, _signum: int, _frame: Any) -> None:
        if self._armed and not self._closed:
            self._armed = False
            self._timed_out = True
            raise self._timeout_error()

    def _fire_async(self, generation: int) -> None:
        with self._lock:
            if self._armed and not self._closed and generation == self._generation:
                self._armed = False
                self._timed_out = True
                self._set_async_exc(TimeoutError)

    def _set_async_exc(self, exc_type: Any) -> None:
        pythonapi = getattr(ctypes, "pythonapi", None)
        if pythonapi is None:
            return
        set_async = pythonapi.PyThreadState_SetAsyncExc
        obj = ctypes.py_object(exc_type) if exc_type is not None else None
        if set_async(ctypes.c_ulong(self._tid), obj) > 1 and exc_type is not None:
            set_async(ctypes.c_ulong(self._tid), None)

    def _arm_locked(self) -> None:
        if self._closed or self._host_waits:
            return
        if self._timed_out or self.remaining <= 0:
            self._timed_out = True
            if threading.get_ident() == self._tid:
                raise self._timeout_error()
            self._set_async_exc(TimeoutError)
            return
        self._armed = True
        self._generation += 1
        self._started_at = time.monotonic()
        delay = max(self.remaining, 0.001)
        if self._use_signal:
            signal.setitimer(signal.ITIMER_REAL, delay)
        else:
            self._timer = threading.Timer(delay, self._fire_async, args=(self._generation,))
            self._timer.daemon = True
            self._timer.start()

    def _disarm_locked(self) -> None:
        if self._armed:
            self._armed = False
            self.remaining = max(0.0, self.remaining - max(0.0, time.monotonic() - self._started_at))
        self._generation += 1
        if self._use_signal:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def __enter__(self) -> "_LocalExecTimer":
        if self._use_signal:
            self._prev_handler = signal.getsignal(signal.SIGALRM)
            self._prev_timer = signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._handle_alarm)
        with self._lock:
            self._arm_locked()
        _STATE["exec_timer"] = self
        return self

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        try:
            if _STATE.get("exec_timer") is self:
                _STATE["exec_timer"] = None
            with self._lock:
                self._closed = True
                self._disarm_locked()
        finally:
            if self._use_signal:
                signal.signal(signal.SIGALRM, self._prev_handler)
                if any(self._prev_timer):
                    signal.setitimer(signal.ITIMER_REAL, *self._prev_timer)
            else:
                self._set_async_exc(None)
        if self._timed_out and exc_type is None:
            raise self._timeout_error()

    def pause(self) -> bool:
        with self._lock:
            if self._closed or self._timed_out:
                return False
            if not self._host_waits:
                self._disarm_locked()
                if self.remaining <= 0:
                    self._arm_locked()  # Deliver expiry to the execution thread, including from a worker.
                    return False
            self._host_waits += 1
            return True

    def resume(self) -> None:
        with self._lock:
            self._host_waits -= 1
            if not self._host_waits:
                self._arm_locked()


class _HostCallError(RuntimeError):
    """A failed bridge reply, with an opaque receipt issued by the host."""

    def __init__(self, message: str, call_id: str, receipt: Any) -> None:
        super().__init__(message)
        self.call_id = call_id
        self.receipt = receipt


def _bridge_call(tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
    """Call a host tool and return its reply value, waiting under the execution's deadline policy.

    A missing bound waits indefinitely; absent or partially written replies are retried.

    Raises:
        ValueError: The encoded request exceeds the body limit.
        TimeoutError: No readable reply arrives within the host-call bound.
        RuntimeError: The host reply contains an error.

    Request-serialization and reply-removal errors propagate.
    """
    request = {"id": uuid.uuid4().hex, "tool": tool_name, "args": args, "kwargs": kwargs}
    if len(json.dumps(request, separators=(",", ":")).encode("utf-8")) > MAX_BODY:
        raise ValueError("Host tool request exceeds the size limit")
    path = os.path.join(REPLY_DIR, request["id"] + ".json")
    timer = _STATE.get("exec_timer")
    paused = timer is not None and timer.pause()
    try:
        _emit(CALL_MARK, request)
        # The Turn's remaining deadline is the only bound: a deadline-free Turn waits without one, and no
        # component substitutes a fixed cutoff. The wait turns a lost reply into an error the model can act
        # on instead of a stall once a deadline exists.
        limit = _host_call_limit(_STATE.get("host_call_timeout"))
        deadline = None if limit is None else time.monotonic() + limit
        delay = 0.005
        while True:
            try:
                with open(path, encoding="utf-8") as handle:
                    reply = json.load(handle)
            except (OSError, ValueError):  # absent, or the upload is still in progress
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"Host tool {tool_name!r} did not reply within {limit:.0f}s") from None
                time.sleep(delay)
                delay = min(delay * 2, 0.05)
                continue
            os.unlink(path)
            if reply.get("error"):
                raise _HostCallError(str(reply["error"])[:2000], request["id"], reply.get("host_error_id"))
            return reply.get("value")
    finally:
        if paused:
            timer.resume()


def _make_tool(tool_name: str) -> Any:
    def tool(*args: Any, **kwargs: Any) -> Any:
        return _bridge_call(tool_name, list(args), kwargs)

    tool.__name__ = tool_name
    return tool


def _record_skill_receipt(helper_name: str, skill: str | None, call: Any) -> Any:
    """Record one bounded skill receipt without exposing instruction bodies, arguments, or results."""
    receipt = {"helper": helper_name, "skill": skill[:64] if isinstance(skill, str) else None, "completed": False}
    try:
        value = call()
        receipt["completed"] = True
        return value
    finally:
        with _WRITE_LOCK:
            receipts = _STATE.get("skill_activity")
            if receipts is not None:
                if len(receipts) < MAX_SKILL_RECEIPTS:
                    receipts.append(receipt)
                else:
                    _STATE["skill_activity_truncated"] = True


def _skill_name_from_path(path: Any) -> str | None:
    """Return the skill name when a workspace read path targets skills/<name>/..."""
    if not isinstance(path, str):
        return None
    rel = path.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    workspace_prefix = WORKSPACE.rstrip("/") + "/"
    if rel.startswith(workspace_prefix):
        rel = rel[len(workspace_prefix) :]
    if not rel.startswith("skills/"):
        return None
    parts = [part for part in rel.split("/") if part and part != "."]
    if len(parts) < 2 or parts[0] != "skills" or parts[1] == "..":
        return None
    return parts[1][:64]


class _ReceiptWorkspace:
    """Proxy workspace reads so direct reads under skills/ emit bounded skill_activity receipts."""

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    def _read(self, method: str, path: Any, max_bytes: int, offset: int) -> Any:
        fn = getattr(self._workspace, method)
        skill = _skill_name_from_path(path)
        if skill is None:
            return fn(path, max_bytes=max_bytes, offset=offset)
        return _record_skill_receipt(f"workspace.{method}", skill, lambda: fn(path, max_bytes=max_bytes, offset=offset))

    def read(self, path: Any, max_bytes: int = 100000, offset: int = 0) -> Any:
        return self._read("read", path, max_bytes, offset)

    def read_text(self, path: Any, max_bytes: int = 100000, offset: int = 0) -> Any:
        return self._read("read_text", path, max_bytes, offset)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._workspace, name)

    def __repr__(self) -> str:
        return repr(self._workspace)


def _receipt_skill_helper(name: str, helper: Any) -> Any:
    """Record actual helper invocations, excluding instruction bodies, arguments, and results."""
    if name == "list_skills":
        return helper

    @functools.wraps(helper)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        skill = args[0] if args else kwargs.get("name")
        return _record_skill_receipt(name, skill if isinstance(skill, str) else None, lambda: helper(*args, **kwargs))

    return invoke


def _submit(**kwargs: Any) -> None:
    raise FinalOutput(kwargs)


def _typed_submit(names: list[str]) -> Any:
    """SUBMIT over the run's output fields, accepting positional or keyword values like DSPy's interpreters."""

    def submit(*args: Any, **kwargs: Any) -> None:
        if args and kwargs:
            raise TypeError("SUBMIT accepts positional or keyword values, not both")
        values = dict(zip(names, args, strict=False)) if args else dict(kwargs)
        if len(args) > len(names) or set(values) != set(names):
            raise TypeError(f"SUBMIT fields do not match the output fields: SUBMIT({', '.join(names)})")
        raise FinalOutput(values)

    return submit


def _jev_decide_batched(items: Any, question: Any, choices: Any = None) -> Any:
    choice_list = None if choices is None else list(choices)
    return _bridge_call("jev_decide_batched", [list(items), question, choice_list], {})


def _make_namespace(tool_names: list[str]) -> dict[str, Any]:
    """Create the module-backed execution globals with workspace helpers and registered host tools."""
    module = types.ModuleType("__fleet_repl__")
    sys.modules[module.__name__] = module
    _STATE["module"] = module
    namespace: dict[str, Any] = module.__dict__
    context_globals = globals()
    namespace.update(
        {
            "__builtins__": vars(builtins).copy(),
            "WORKSPACE": WORKSPACE,
            "llm_query": _make_tool("llm_query"),
            "llm_query_batched": _make_tool("llm_query_batched"),
            "jev_decide_batched": _jev_decide_batched,
            "SUBMIT": _submit,
        }
    )
    namespace.update(
        {name: _receipt_skill_helper(name, context_globals[name]) for name in SKILL_HELPERS if name in context_globals}
    )
    if "workspace" in context_globals:
        namespace.update(
            workspace=_ReceiptWorkspace(context_globals["workspace"]),
            WorkspaceDatasetView=context_globals["WorkspaceDatasetView"],
        )
    dynamic_tools: set[str] = set()
    for name in tool_names:
        if name.isidentifier() and not keyword.iskeyword(name) and name not in RESERVED_NAMES:
            namespace[name] = _make_tool(name)
            dynamic_tools.add(name)
    _STATE["registered_tools"] = dynamic_tools
    return namespace


def _invalid_bindings(variables: Any, tools: Any) -> bool:
    reserved_tool_names = RESERVED_NAMES - REGISTERED_BUILTIN_TOOLS
    return (
        not isinstance(variables, dict)
        or len(variables) > 1000
        or any(not isinstance(key, str) for key in variables)
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
    )


def _run(code: str, variables: dict[str, Any], tools: list[str], outputs: Any = None) -> dict[str, Any]:
    """Execute a cell in the persistent namespace and return captured output and result metadata.

    Valid variables update unreserved bindings; `outputs` names constrain SUBMIT fields.
    Returns validation and user-code errors as result dictionaries, including the remote
    error type when available. SUBMIT supplies `final`; otherwise the last expression
    supplies `has_value` and a JSON-compatible `value`. Output is bounded and Skill activity
    is recorded.
    """
    _STATE["skill_activity"] = []
    _STATE["skill_activity_truncated"] = False
    if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE:
        return {"error": True, "error_type": "ValueError", "stderr": "invalid or oversized code"}
    if _invalid_bindings(variables, tools):
        return {"error": True, "error_type": "ValueError", "stderr": "invalid execution bindings"}
    if outputs is not None and (
        not isinstance(outputs, list)
        or len(outputs) > 64
        or any(not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) for name in outputs)
    ):
        return {"error": True, "error_type": "ValueError", "stderr": "invalid output fields"}
    namespace = _STATE["namespace"]
    if namespace is None:
        namespace = _STATE["namespace"] = _make_namespace(tools)
    previous_tools: set[str] = _STATE.get("registered_tools") or set()
    current_dynamic = {name for name in tools if name not in RESERVED_NAMES}
    for stale in previous_tools - current_dynamic:
        namespace.pop(stale, None)
    _STATE["registered_tools"] = current_dynamic
    namespace["__builtins__"] = vars(builtins).copy()
    namespace["llm_query"] = _make_tool("llm_query")
    namespace["llm_query_batched"] = _make_tool("llm_query_batched")
    namespace["jev_decide_batched"] = _jev_decide_batched
    namespace["SUBMIT"] = _typed_submit(outputs) if outputs else _submit
    protected = RESERVED_NAMES | set(tools)
    for name in current_dynamic:
        namespace[name] = _make_tool(name)
    if "__dspy_guest_spec" in namespace and "__dspy_spec" not in variables:
        guest_mod = sys.modules.get("dspy.primitives.sandbox_guest")
        if guest_mod is not None:
            guest_mod.bind(namespace)
    namespace.update(
        {
            key: value
            for key, value in variables.items()
            if key.isidentifier() and not keyword.iskeyword(key) and key not in protected
        }
    )
    output, errors = BoundedBuffer(), BoundedBuffer()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            tree = ast.parse(code, mode="exec")
            last = tree.body[-1] if tree.body else None
            if isinstance(last, ast.Expr):
                tree.body.pop()
            else:
                last = None
            limit = _exec_limit()
            with _LocalExecTimer(limit) if limit is not None else contextlib.nullcontext():
                exec(compile(tree, "<fleet_rlm>", "exec"), namespace, namespace)
                value = (
                    eval(compile(ast.Expression(last.value), "<fleet_rlm>", "eval"), namespace)
                    if last is not None
                    else None
                )
        result: dict[str, Any] = {
            "stdout": output.getvalue(),
            "stderr": errors.getvalue(),
            "final": None,
            "has_value": last is not None,
            "value": _jsonable(value),
        }
    except FinalOutput as final:
        try:
            if len(json.dumps(final.payload, separators=(",", ":")).encode("utf-8")) > MAX_BODY:
                raise ValueError("final output exceeds the size limit")
            result = {"stdout": output.getvalue(), "stderr": errors.getvalue(), "final": final.payload}
        except (TypeError, ValueError) as exc:
            errors.write(str(exc))
            result = {"stdout": output.getvalue(), "stderr": errors.getvalue(), "error": True}
    except BaseException as exc:
        errors.write(traceback.format_exc())
        result = {
            "stdout": output.getvalue(),
            "stderr": errors.getvalue(),
            "error": True,
            "error_type": type(exc).__name__,
        }
        if isinstance(exc, _HostCallError):
            result["host_call_id"] = exc.call_id
            result["host_error_id"] = exc.receipt
    if output.truncated or errors.truncated:
        result["truncated"] = True
    result["skill_activity"] = _STATE["skill_activity"]
    result["skill_activity_truncated"] = _STATE["skill_activity_truncated"]
    return result


def _fleet_exec(payload_json: str) -> None:
    """Run one execution and report it as a single FLEET_RESULT line."""
    _STATE["stdout"] = sys.stdout  # the interpreter's per-run stream; markers must reach it, not the capture
    try:
        payload = json.loads(payload_json)
        _STATE["host_call_timeout"] = payload.get("host_call_timeout")
        _STATE["exec_timeout"] = payload.get("exec_timeout")
        result = _run(
            payload.get("code"), payload.get("variables") or {}, payload.get("tools") or [], payload.get("outputs")
        )
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_BODY - 1024:
            result = {"error": True, "error_type": "ValueError", "stderr": "Execution result exceeds response limit"}
        _emit(RESULT_MARK, result)
    finally:
        _STATE["stdout"] = None
        _STATE["exec_timeout"] = None


os.makedirs(REPLY_DIR, mode=0o700, exist_ok=True)
if os.path.islink(REPLY_DIR) or not os.path.isdir(REPLY_DIR):
    raise RuntimeError("Fleet sandbox reply path must be a directory, not a symlink")
os.chmod(REPLY_DIR, 0o700)
if os.path.isdir(WORKSPACE):
    for _directory in ("files", "artifacts", "memory", "skills", "buffers", "sessions"):
        os.makedirs(os.path.join(WORKSPACE, _directory), exist_ok=True)
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
with contextlib.suppress(ImportError):
    import dspy as _preloaded_dspy  # noqa: F401
