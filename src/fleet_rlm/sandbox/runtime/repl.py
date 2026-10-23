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
import functools
import io
import json
import keyword
import math
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any

CALL_MARK = "\x1eFLEET_CALL "
RESULT_MARK = "\x1eFLEET_RESULT "
PROGRESS_MARK = "\x1eFLEET_PROGRESS "
REPLY_DIR = "/tmp/fleet/replies"
WORKSPACE = "/workspace"
MAX_BODY = 2_000_000
MAX_CODE = 1_000_000
MAX_CAPTURE_CHARS = 100_000
MAX_PROGRESS_LABEL = 64
MAX_PROGRESS_EXPECT_SECONDS = 86_400
MAX_PROGRESS_CHECKPOINT = 2**63 - 1
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
    "progress_scope",
}
# DSPy Flex lists these existing interpreter APIs as registered tools.
REGISTERED_BUILTIN_TOOLS = {"llm_query", "llm_query_batched", "jev_decide_batched"}
SKILL_HELPERS = ("list_skills", "use_skill", "read_skill_reference", "run_skill_script", "save_skill")
MAX_SKILL_RECEIPTS = 20

_STATE: dict[str, Any] = {"namespace": None, "stdout": None, "host_call_timeout": None}
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


def _progress_label(value: Any) -> str:
    """Return a bounded, non-empty progress label or refuse the value with its rule."""
    if not isinstance(value, str):
        raise TypeError(f"progress_scope label must be a string, not {type(value).__name__}")
    label = value.strip()
    if not label:
        raise ValueError("progress_scope label must be a non-empty string")
    if len(label) > MAX_PROGRESS_LABEL:
        raise ValueError(f"progress_scope label must be at most {MAX_PROGRESS_LABEL} characters")
    return label


def _progress_expectation(value: Any) -> float:
    """Return a finite positive expectation in seconds; booleans and text never coerce."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"progress_scope expect_seconds must be a number, not {type(value).__name__}")
    expectation = float(value)
    if not math.isfinite(expectation) or expectation <= 0:
        raise ValueError("progress_scope expect_seconds must be a finite positive number")
    if expectation > MAX_PROGRESS_EXPECT_SECONDS:
        raise ValueError(f"progress_scope expect_seconds must be at most {MAX_PROGRESS_EXPECT_SECONDS}")
    return expectation


def _emit_progress(scope: "_ProgressScope", phase: str, checkpoint: Any = None) -> None:
    """Report one bounded progress observation; this never raises into user code."""
    _emit(
        PROGRESS_MARK,
        {
            "phase": phase,
            "scope": scope.label,
            "expect_seconds": scope.expect_seconds,
            "checkpoint": checkpoint,
        },
    )


class _ProgressScope:
    """Checkpoint-only observation window: it reports, and it never acts on the cell.

    The expectation is immutable, values must advance past the high-water mark to renew,
    and a handle from an earlier cell is closed, so it can never renew a later label.
    """

    __slots__ = ("_closed", "_entered", "_expect_seconds", "_high_water", "_label")

    def __init__(self, label: Any, expect_seconds: Any) -> None:
        self._label = _progress_label(label)
        self._expect_seconds = _progress_expectation(expect_seconds)
        self._high_water = -1
        self._entered = False
        self._closed = False
        self._require_main_thread()

    @property
    def label(self) -> str:
        """The bounded scope label."""
        return self._label

    @property
    def expect_seconds(self) -> float:
        """The immutable expectation that only labels `stalled`; it changes nothing."""
        return self._expect_seconds

    def _require_main_thread(self) -> None:
        # The interpreter worker's execution thread is this cell's main thread; a user thread never is.
        expected = _STATE.get("execution_thread")
        if expected is not None and threading.get_ident() != expected:
            raise RuntimeError("progress_scope is only available on the interpreter's main thread")

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stack = _STATE.get("progress_stack")
        if stack is not None and self in stack:
            stack.remove(self)
        if self._entered:
            _emit_progress(self, "exit")

    def __enter__(self) -> "_ProgressScope":
        self._require_main_thread()
        if self._closed:
            raise RuntimeError("progress_scope has already ended")
        if self._entered:
            raise RuntimeError("progress_scope cannot be entered twice")
        stack = _STATE.get("progress_stack")
        if stack is None:
            stack = _STATE["progress_stack"] = []
        self._entered = True
        stack.append(self)
        _emit_progress(self, "start")
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self._close()
        return False

    def checkpoint(self, value: Any) -> int:
        """Report a strictly increasing non-negative integer; repeats never renew the expectation."""
        self._require_main_thread()
        if self._closed:
            raise RuntimeError("progress_scope has ended; checkpoints from a previous cell do not renew")
        if not self._entered:
            raise RuntimeError("progress_scope must be entered with 'with' before checkpoints")
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"progress checkpoint must be an integer, not {type(value).__name__}")
        if value < 0:
            raise ValueError("progress checkpoint must be a non-negative integer")
        if value > MAX_PROGRESS_CHECKPOINT:
            raise ValueError(f"progress checkpoint must be at most {MAX_PROGRESS_CHECKPOINT}")
        if value <= self._high_water:
            # A repeated or lower value is evidence, not a fresh expectation window.
            return self._high_water
        self._high_water = value
        _emit_progress(self, "checkpoint", checkpoint=value)
        return value


def progress_scope(label: Any, expect_seconds: Any) -> _ProgressScope:
    """Open a checkpoint-only observation scope; it never cancels, pauses, or shortens the cell."""
    return _ProgressScope(label, expect_seconds)


def _close_progress_scopes() -> None:
    """End every scope this cell opened, so no handle survives into the next cell."""
    stack = _STATE.pop("progress_stack", None)
    for scope in list(stack or []):
        scope._close()


def _host_call_limit(value: Any) -> float | None:
    """Return the host-call wait in seconds, or None when the Turn has no Fleet deadline.

    The selection uses an `is None` check, so a supplied number is used as given instead of being
    replaced by a fixed fallback. `0` is the transport's explicit "no timeout" value, so it means
    no bound as well; any other positive value is the Turn's remaining deadline.
    """
    if value is None or value <= 0:
        return None
    return float(value)


def _bridge_call(tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
    request = {"id": uuid.uuid4().hex, "tool": tool_name, "args": args, "kwargs": kwargs}
    if len(json.dumps(request, separators=(",", ":")).encode("utf-8")) > MAX_BODY:
        raise ValueError("Host tool request exceeds the size limit")
    path = os.path.join(REPLY_DIR, request["id"] + ".json")
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
            raise RuntimeError(str(reply["error"])[:2000])
        return reply.get("value")


def _make_tool(tool_name: str) -> Any:
    def tool(*args: Any, **kwargs: Any) -> Any:
        return _bridge_call(tool_name, list(args), kwargs)

    tool.__name__ = tool_name
    return tool


def _receipt_skill_helper(name: str, helper: Any) -> Any:
    """Record actual helper invocations, excluding instruction bodies, arguments, and results."""
    if name == "list_skills":
        return helper

    @functools.wraps(helper)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        skill = args[0] if args else kwargs.get("name")
        receipt = {"helper": name, "skill": skill[:64] if isinstance(skill, str) else None, "completed": False}
        try:
            value = helper(*args, **kwargs)
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


def _make_namespace(tool_names: list[str]) -> dict[str, Any]:
    context_globals = globals()
    namespace: dict[str, Any] = {
        "WORKSPACE": WORKSPACE,
        "llm_query": lambda prompt: _bridge_call("llm_query", [prompt], {}),
        "llm_query_batched": lambda prompts: _bridge_call("llm_query_batched", [list(prompts)], {}),
        "jev_decide_batched": lambda items, question, choices: _bridge_call(
            "jev_decide_batched", [list(items), question, list(choices)], {}
        ),
        "SUBMIT": _submit,
        "progress_scope": progress_scope,
    }
    namespace.update(
        {name: _receipt_skill_helper(name, context_globals[name]) for name in SKILL_HELPERS if name in context_globals}
    )
    if "workspace" in context_globals:
        namespace.update(
            workspace=context_globals["workspace"], WorkspaceDatasetView=context_globals["WorkspaceDatasetView"]
        )

    # DSPy's remote facade registers a process-global import; keep this namespace's import bound to its facade.
    def namespace_import(
        name: str, module_globals: Any = None, module_locals: Any = None, fromlist: Any = (), level: int = 0
    ) -> Any:
        if level == 0 and name == "dspy" and "dspy" in namespace:
            return namespace["dspy"]
        return builtins.__import__(name, module_globals, module_locals, fromlist, level)

    namespace["__builtins__"] = dict(vars(builtins), __import__=namespace_import)
    for name in tool_names:
        if name.isidentifier() and not keyword.iskeyword(name) and name not in namespace:
            namespace[name] = _make_tool(name)
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
    _STATE["skill_activity"] = []
    _STATE["skill_activity_truncated"] = False
    # Scopes are execution-local: the new cell starts with none, whatever the previous cell opened.
    _close_progress_scopes()
    _STATE["progress_stack"] = []
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
    namespace["SUBMIT"] = _typed_submit(outputs) if outputs else _submit
    # Reserved: a user binding from an earlier cell must not shadow the progress helper.
    namespace["progress_scope"] = progress_scope
    protected = RESERVED_NAMES | set(tools)
    for name in tools:
        if name not in RESERVED_NAMES:
            namespace[name] = _make_tool(name)
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
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            assert last is None or isinstance(last, ast.Expr)
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
    if output.truncated or errors.truncated:
        result["truncated"] = True
    result["skill_activity"] = _STATE["skill_activity"]
    result["skill_activity_truncated"] = _STATE["skill_activity_truncated"]
    # Closing here covers a cell that raised or returned while a scope was still open: the handle
    # remains in the namespace but belongs to this execution, and the next cell starts unknown.
    _close_progress_scopes()
    return result


def _fleet_exec(payload_json: str) -> None:
    """Run one execution and report it as a single FLEET_RESULT line."""
    _STATE["stdout"] = sys.stdout  # the interpreter's per-run stream; markers must reach it, not the capture
    _STATE["execution_thread"] = threading.get_ident()
    try:
        payload = json.loads(payload_json)
        _STATE["host_call_timeout"] = payload.get("host_call_timeout")
        result = _run(
            payload.get("code"), payload.get("variables") or {}, payload.get("tools") or [], payload.get("outputs")
        )
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_BODY - 1024:
            result = {"error": True, "error_type": "ValueError", "stderr": "Execution result exceeds response limit"}
        _emit(RESULT_MARK, result)
    finally:
        _STATE["stdout"] = None


os.makedirs(REPLY_DIR, exist_ok=True)
if os.path.isdir(WORKSPACE):
    for _directory in ("files", "artifacts", "memory", "skills", "buffers", "sessions"):
        os.makedirs(os.path.join(WORKSPACE, _directory), exist_ok=True)
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
