"""Sandbox driver for code execution via JSON protocol.

This module provides a long-lived JSON protocol driver that runs inside a
sandbox. It receives code execution commands via stdin, executes them in a
controlled environment, and returns results via stdout.

The driver supports:
    - Stateful code execution (globals persist across calls)
    - Tool registration and invocation
    - Output capture (stdout/stderr)
    - Structured final output via SUBMIT function
    - Final variable convention (see below)

Protocol:
    Input (JSON, one per line):
        {
            "code": "python code to execute",
            "variables": {"var_name": value},  // Optional
            "tool_names": ["tool1", "tool2"],  // Optional
            "output_names": ["result1", "result2"],  // Optional
            "execution_profile": "ROOT_INTERLOCUTOR|RLM_ROOT|RLM_DELEGATE|MAINTENANCE"  // Optional
        }

    Output (JSON, one per line):
        {
            "stdout": "captured stdout",
            "stderr": "captured stderr",
            "final": {...}  // Structured output from SUBMIT or Final
        }

    Tool calls (output):
        {"tool_call": {"name": "tool_name", "args": [...], "kwargs": {...}}}

    Tool responses (input):
        {"tool_result": ...} or {"tool_error": "error message"}

Final Variable Convention:
    As described in the RLM paper (Section 2), code executed in the REPL can
    signal completion by setting a variable named ``Final``. When the driver
    detects that ``Final`` has been set in the globals after code execution,
    it automatically returns the value of ``Final`` as the structured output
    and stops further iteration.

    This provides a natural way for LLM-generated code to indicate completion:

        >>> analysis = process_document(text)
        >>> Final = {"result": analysis, "status": "complete"}

    The driver will detect ``Final``, return its value, and terminate the
    session. If ``Final`` is not set, execution continues normally (backwards
    compatible with SUBMIT-based workflows).
"""

from __future__ import annotations


def sandbox_driver() -> None:
    """Run the long-lived JSON protocol driver for sandbox execution.

    This function runs an infinite loop reading JSON commands from stdin,
    executing Python code, and writing results to stdout. It maintains
    state across executions through sandbox_globals.

    The driver provides these built-in capabilities to executed code:
        - SUBMIT(): Function to return structured final output
        - Final variable: Set a variable named ``Final`` to return structured
          output (alternative to SUBMIT, as per RLM paper Section 2)
        - llm_query(prompt): Query a sub-LLM for semantic analysis (via tool_call)
        - llm_query_batched(prompts): Query multiple prompts concurrently (via tool_call)
        - Tool functions: Dynamically registered based on tool_names in commands

    The loop terminates on EOFError (stdin closed) or when ``Final`` is set
    (if the caller stops sending commands after receiving a Final response).
    """

    # Session history helpers (inlined from session_history.py)
    _session_history: list[dict[str, Any]] = []

    def log_execution(
        code: str,
        result: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log code execution to session history for tracking and learning."""
        entry = {
            "timestamp": time.time(),
            "code_preview": code[:200] + "..." if len(code) > 200 else code,
            "stdout_preview": result.get("stdout", "")[:200],
            "stderr_preview": result.get("stderr", "")[:200],
            "had_final": result.get("final") is not None,
            "metadata": metadata or {},
        }
        _session_history.append(entry)

    def get_session_history() -> list[dict[str, Any]]:
        """Return all logged executions in this session."""
        return list(_session_history)

    def get_last_execution() -> dict[str, Any] | None:
        """Return the most recent execution entry, or None if empty."""
        return _session_history[-1] if _session_history else None

    def reset_session_history() -> None:
        """Reset session history."""
        _session_history.clear()

    # Inlined factory functions so ``inspect.getsource(sandbox_driver)`` is
    # self-contained when executed in the sandbox process.
    class FinalOutput(BaseException):
        """Exception to signal final output from SUBMIT call."""

        pass

    RESERVED_TOOL_NAMES = frozenset({"llm_query", "llm_query_batched", "SUBMIT", "print"})

    def make_send(proto_out: Any) -> Callable[[dict], None]:
        def _send(obj: dict) -> None:
            if proto_out is None:
                return
            proto_out.write(json.dumps(obj) + "\n")
            proto_out.flush()

        return _send

    def make_tool_call(send: Callable[[dict], None]) -> Callable[..., Any]:
        def _tool_call(name: str, *args, **kwargs) -> Any:
            send({"tool_call": {"name": name, "args": list(args), "kwargs": kwargs}})
            reply = json.loads(input())
            if reply.get("tool_error"):
                raise RuntimeError(reply["tool_error"])
            return reply.get("tool_result")

        return _tool_call

    def wrap_helper(fn: Callable[..., Any], current_profile: list[str]) -> Callable[..., Any]:
        fn_name = getattr(fn, "__name__", "unknown")

        def _wrapped(*args, **kwargs):
            if current_profile[0] == "ROOT_INTERLOCUTOR":
                raise RuntimeError(
                    f"Helper '{fn_name}' is not available in ROOT_INTERLOCUTOR profile. "
                    "Delegate tool-heavy work via llm_query/llm_query_batched."
                )
            return fn(*args, **kwargs)

        _wrapped.__name__ = fn_name
        _wrapped.__doc__ = getattr(fn, "__doc__", None)
        return _wrapped

    def make_submit(output_names: list[str]) -> Callable[..., None]:
        def SUBMIT(*args, **kwargs) -> None:
            if kwargs:
                raise FinalOutput(kwargs)
            if not output_names:
                if len(args) == 1:
                    raise FinalOutput({"output": args[0]})
                raise FinalOutput({"output": list(args)})
            if len(args) != len(output_names):
                raise FinalOutput(
                    {
                        "error": f"SUBMIT expected {len(output_names)} positional values ({output_names}), got {len(args)}"
                    }
                )
            raise FinalOutput(dict(zip(output_names, args)))

        return SUBMIT

    def make_llm_query(tool_call: Callable[..., Any]) -> Callable[[str], str]:
        def llm_query(prompt: str) -> str:
            return tool_call("llm_query", prompt)

        return llm_query

    def make_llm_query_batched(
        tool_call: Callable[..., Any],
    ) -> Callable[[list[str]], list[str]]:
        def llm_query_batched(prompts: list[str]) -> list[str]:
            return tool_call("llm_query_batched", prompts)

        return llm_query_batched

    def register_tools(
        names: list[str],
        sandbox_globals: dict[str, Any],
        dynamic_tool_names: set[str],
        tool_call: Callable[..., Any],
        current_profile: list[str],
    ) -> None:
        if current_profile[0] == "ROOT_INTERLOCUTOR":
            for dyn_name in list(dynamic_tool_names):
                sandbox_globals.pop(dyn_name, None)
            dynamic_tool_names.clear()
            return
        for name in names:
            if not name.isidentifier() or name in RESERVED_TOOL_NAMES:
                continue
            if name in sandbox_globals:
                continue

            def _make(name_: str):
                def _fn(*args, **kwargs):
                    return tool_call(name_, *args, **kwargs)

                return _fn

            sandbox_globals[name] = _make(name)
            dynamic_tool_names.add(name)

    def inject_sandbox_helpers(
        sandbox_globals: dict[str, Any],
        wrap_fn: Callable[[Callable], Callable],
        sandbox_tools: dict[str, Callable],
        volume_tools: dict[str, Callable],
        session_tools: dict[str, Callable],
    ) -> None:
        for name, fn in sandbox_tools.items():
            sandbox_globals[name] = wrap_fn(fn)
        for name, fn in volume_tools.items():
            sandbox_globals[name] = wrap_fn(fn)
        for name, fn in session_tools.items():
            sandbox_globals[name] = wrap_fn(fn)

    # Keep remaining imports inside the function so the source extracted by
    # ``inspect.getsource(sandbox_driver)`` is self-contained when executed
    # in the sandbox process.
    import json
    import sys
    import time
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from typing import Any, Callable, cast

    try:
        from fleet_rlm.runtime.execution.sandbox_assets import (
            add_buffer,
            chunk_by_headers,
            chunk_by_size,
            clear_buffer,
            get_buffer,
            grep,
            load_from_volume,
            peek,
            reset_buffers,
            save_to_volume,
            workspace_append,
            workspace_list,
            workspace_read,
            workspace_write,
        )
    except ModuleNotFoundError:
        # The interpreter may execute a bundled script that already
        # defines these symbols in globals without an installed fleet_rlm package.
        # Access them from globals() instead.
        g: dict[str, Any] = globals()
        add_buffer = cast(Any, g.get("add_buffer"))
        chunk_by_headers = cast(Any, g.get("chunk_by_headers"))
        chunk_by_size = cast(Any, g.get("chunk_by_size"))
        clear_buffer = cast(Any, g.get("clear_buffer"))
        get_buffer = cast(Any, g.get("get_buffer"))
        grep = cast(Any, g.get("grep"))
        peek = cast(Any, g.get("peek"))
        reset_buffers = cast(Any, g.get("reset_buffers"))
        load_from_volume = cast(Any, g.get("load_from_volume"))
        save_to_volume = cast(Any, g.get("save_to_volume"))
        workspace_append = cast(Any, g.get("workspace_append"))
        workspace_list = cast(Any, g.get("workspace_list"))
        workspace_read = cast(Any, g.get("workspace_read"))
        workspace_write = cast(Any, g.get("workspace_write"))

    # Reset module-level state for fresh start (each driver instance is independent)
    reset_session_history()
    reset_buffers()

    # Persistent globals that survive across code execution calls
    sandbox_globals: dict[str, Any] = {}
    proto_out = sys.__stdout__

    # Use list for mutable reference in closure
    current_execution_profile = ["RLM_DELEGATE"]
    _dynamic_tool_names: set[str] = set()

    output_names: list[str] = []

    # Create protocol functions
    _send = make_send(proto_out)
    _tool_call = make_tool_call(_send)

    # Create wrapped helper injector
    def _wrap(fn: Callable) -> Callable:
        return wrap_helper(fn, current_execution_profile)

    # Inject sandbox helpers into globals
    inject_sandbox_helpers(
        sandbox_globals,
        _wrap,
        {
            "peek": peek,
            "grep": grep,
            "chunk_by_size": chunk_by_size,
            "chunk_by_headers": chunk_by_headers,
            "add_buffer": add_buffer,
            "get_buffer": get_buffer,
            "clear_buffer": clear_buffer,
        },
        {
            "save_to_volume": save_to_volume,
            "load_from_volume": load_from_volume,
            "workspace_write": workspace_write,
            "workspace_read": workspace_read,
            "workspace_list": workspace_list,
            "workspace_append": workspace_append,
        },
        {
            "log_execution": log_execution,
            "get_session_history": get_session_history,
            "get_last_execution": get_last_execution,
        },
    )

    # Main execution loop
    while True:
        try:
            line = input()
        except EOFError:
            break

        try:
            command = json.loads(line)
        except json.JSONDecodeError as exc:
            _send({"stdout": "", "stderr": f"[Error] Invalid JSON: {exc}", "final": None})
            continue

        code = command.get("code")
        variables = command.get("variables", {}) or {}
        tool_names = list(command.get("tool_names", []) or [])
        output_names = list(command.get("output_names", []) or [])
        execution_profile = str(command.get("execution_profile", "RLM_DELEGATE")).strip()
        if execution_profile not in {
            "ROOT_INTERLOCUTOR",
            "RLM_ROOT",
            "RLM_DELEGATE",
            "MAINTENANCE",
        }:
            execution_profile = "RLM_DELEGATE"
        current_execution_profile[0] = execution_profile

        if code is None:
            _send({"stdout": "", "stderr": "[Error] No code provided", "final": None})
            continue

        # Create SUBMIT for this execution
        SUBMIT = make_submit(output_names)
        sandbox_globals["SUBMIT"] = SUBMIT

        # Create LLM query functions
        llm_query = make_llm_query(_tool_call)
        llm_query_batched = make_llm_query_batched(_tool_call)
        sandbox_globals["llm_query"] = llm_query
        sandbox_globals["llm_query_batched"] = llm_query_batched

        sandbox_globals.update(variables)
        register_tools(
            tool_names,
            sandbox_globals,
            _dynamic_tool_names,
            _tool_call,
            current_execution_profile,
        )

        stdout_io = StringIO()
        stderr_io = StringIO()
        final_obj = None

        had_exec_error = False
        with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
            try:
                exec(code, sandbox_globals)
            except FinalOutput as exc:
                final_obj = exc.args[0] if exc.args else None
            except Exception as exc:  # pragma: no cover
                had_exec_error = True
                print(f"[Error] {type(exc).__name__}: {exc}", file=sys.stderr)

        # Final Variable Convention: Check if 'Final' was set in globals.
        # Always clear it after execution to prevent stale values leaking into
        # later commands in this stateful session.
        if had_exec_error:
            sandbox_globals.pop("Final", None)
        else:
            _missing = object()
            final_from_var = sandbox_globals.pop("Final", _missing)
            if final_obj is None and final_from_var is not _missing:
                final_obj = final_from_var

        result = {
            "stdout": stdout_io.getvalue(),
            "stderr": stderr_io.getvalue(),
            "final": final_obj,
        }

        # Log execution for session history tracking
        log_execution(code, result, {"had_error": had_exec_error})

        _send(result)
