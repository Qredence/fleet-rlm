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
import io
import json
import keyword
import os
import sys
import threading
import time
import traceback
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
# DSPy Flex lists these existing interpreter APIs as registered tools.
REGISTERED_BUILTIN_TOOLS = {"llm_query", "llm_query_batched", "jev_decide_batched"}
SKILL_HELPERS = ("list_skills", "use_skill", "read_skill_reference", "run_skill_script", "save_skill")

_STATE: dict[str, Any] = {"namespace": None, "stdout": None}
_WRITE_LOCK = threading.Lock()


class BoundedBuffer(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.truncated = False

    def write(self, value: str) -> int:
        remaining = MAX_CAPTURE_CHARS - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(value)
        written = super().write(value[:remaining])
        if len(value) > written:
            self.truncated = True
        return len(value)


class FinalOutput(BaseException):
    def __init__(self, payload: Any) -> None:
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


def _bridge_call(tool_name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
    request = {"id": uuid.uuid4().hex, "tool": tool_name, "args": args, "kwargs": kwargs}
    if len(json.dumps(request, separators=(",", ":")).encode("utf-8")) > MAX_BODY:
        raise ValueError("Host tool request exceeds the size limit")
    path = os.path.join(REPLY_DIR, request["id"] + ".json")
    _emit(CALL_MARK, request)
    # The Turn watchdog stops the sandbox; there is deliberately no per-call timer here.
    delay = 0.005
    while True:
        try:
            with open(path, encoding="utf-8") as handle:
                reply = json.load(handle)
        except (OSError, ValueError):  # absent, or the upload is still in progress
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
    }
    namespace.update({name: context_globals[name] for name in SKILL_HELPERS if name in context_globals})
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
    return result


def _fleet_exec(payload_json: str) -> None:
    """Run one execution and report it as a single FLEET_RESULT line."""
    _STATE["stdout"] = sys.stdout  # the interpreter's per-run stream; markers must reach it, not the capture
    try:
        payload = json.loads(payload_json)
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
