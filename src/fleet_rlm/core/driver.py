"""Sandbox driver for Modal code execution via JSON protocol.

This module provides a long-lived JSON protocol driver that runs inside a Modal
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

from .driver_factories import FinalOutput  # noqa: F401 — public re-export


def sandbox_driver() -> None:
    """Run the long-lived JSON protocol driver for Modal Sandbox execution.

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
    # Keep these imports inside the function so the source extracted by
    # ``inspect.getsource(sandbox_driver)`` is self-contained when executed
    # in the Modal sandbox process.
    import json
    import sys
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from typing import Any, Callable, cast

    try:
        from fleet_rlm.core.driver_factories import (
            FinalOutput,
            inject_sandbox_helpers,
            make_llm_query,
            make_llm_query_batched,
            make_send,
            make_submit,
            make_tool_call,
            register_tools,
            wrap_helper,
        )
        from fleet_rlm.core.sandbox_tools import (
            add_buffer,
            chunk_by_headers,
            chunk_by_json_keys,
            chunk_by_size,
            chunk_by_timestamps,
            clear_buffer,
            get_buffer,
            grep,
            peek,
            reset_buffers,
        )
        from fleet_rlm.core.session_history import (
            get_last_execution,
            get_session_history,
            log_execution,
            reset_session_history,
        )
        from fleet_rlm.core.volume_tools import (
            load_from_volume,
            save_to_volume,
            workspace_append,
            workspace_list,
            workspace_read,
            workspace_write,
        )
    except ModuleNotFoundError:
        # In Modal, interpreter may execute a bundled script that already
        # defines these symbols in globals without an installed fleet_rlm package.
        # Access them from globals() instead.
        g: dict[str, Any] = globals()
        FinalOutput = cast(Any, g.get("FinalOutput"))
        inject_sandbox_helpers = cast(Any, g.get("inject_sandbox_helpers"))
        make_llm_query = cast(Any, g.get("make_llm_query"))
        make_llm_query_batched = cast(Any, g.get("make_llm_query_batched"))
        make_send = cast(Any, g.get("make_send"))
        make_submit = cast(Any, g.get("make_submit"))
        make_tool_call = cast(Any, g.get("make_tool_call"))
        register_tools = cast(Any, g.get("register_tools"))
        wrap_helper = cast(Any, g.get("wrap_helper"))
        add_buffer = cast(Any, g.get("add_buffer"))
        chunk_by_headers = cast(Any, g.get("chunk_by_headers"))
        chunk_by_json_keys = cast(Any, g.get("chunk_by_json_keys"))
        chunk_by_size = cast(Any, g.get("chunk_by_size"))
        chunk_by_timestamps = cast(Any, g.get("chunk_by_timestamps"))
        clear_buffer = cast(Any, g.get("clear_buffer"))
        get_buffer = cast(Any, g.get("get_buffer"))
        grep = cast(Any, g.get("grep"))
        peek = cast(Any, g.get("peek"))
        reset_buffers = cast(Any, g.get("reset_buffers"))
        get_last_execution = cast(Any, g.get("get_last_execution"))
        get_session_history = cast(Any, g.get("get_session_history"))
        log_execution = cast(Any, g.get("log_execution"))
        reset_session_history = cast(Any, g.get("reset_session_history"))
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
            "chunk_by_timestamps": chunk_by_timestamps,
            "chunk_by_json_keys": chunk_by_json_keys,
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
            _send(
                {"stdout": "", "stderr": f"[Error] Invalid JSON: {exc}", "final": None}
            )
            continue

        code = command.get("code")
        variables = command.get("variables", {}) or {}
        tool_names = list(command.get("tool_names", []) or [])
        output_names = list(command.get("output_names", []) or [])
        execution_profile = str(
            command.get("execution_profile", "RLM_DELEGATE")
        ).strip()
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
