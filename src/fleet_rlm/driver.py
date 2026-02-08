"""Sandbox driver for Modal code execution via JSON protocol.

This module provides a long-lived JSON protocol driver that runs inside a Modal
sandbox. It receives code execution commands via stdin, executes them in a
controlled environment, and returns results via stdout.

The driver supports:
    - Stateful code execution (globals persist across calls)
    - Tool registration and invocation
    - Output capture (stdout/stderr)
    - Structured final output via SUBMIT function

Protocol:
    Input (JSON, one per line):
        {
            "code": "python code to execute",
            "variables": {"var_name": value},  // Optional
            "tool_names": ["tool1", "tool2"],  // Optional
            "output_names": ["result1", "result2"]  // Optional
        }

    Output (JSON, one per line):
        {
            "stdout": "captured stdout",
            "stderr": "captured stderr",
            "final": {...}  // Structured output from SUBMIT
        }

    Tool calls (output):
        {"tool_call": {"name": "tool_name", "args": [...], "kwargs": {...}}}

    Tool responses (input):
        {"tool_result": ...} or {"tool_error": "error message"}
"""

from __future__ import annotations


def sandbox_driver() -> None:
    """Run the long-lived JSON protocol driver for Modal Sandbox execution.

    This function runs an infinite loop reading JSON commands from stdin,
    executing Python code, and writing results to stdout. It maintains
    state across executions through sandbox_globals.

    The driver provides these built-in capabilities to executed code:
        - SUBMIT(): Function to return structured final output
        - Tool functions: Dynamically registered based on tool_names in commands

    The loop terminates on EOFError (stdin closed).
    """

    import json
    import sys
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from typing import Any

    # Persistent globals that survive across code execution calls
    sandbox_globals: dict[str, Any] = {}
    proto_out = sys.__stdout__

    output_names: list[str] = []

    class _FinalOutput(BaseException):
        """Exception to signal final output from SUBMIT call.

        Used internally to transfer structured output from the sandboxed
        code back to the driver without using normal return mechanisms.
        """

        pass

    def _send(obj: dict) -> None:
        """Send a JSON object to the parent process via stdout."""
        proto_out.write(json.dumps(obj) + "\n")
        proto_out.flush()

    def _tool_call(name: str, *args, **kwargs):
        """Make a tool call and wait for response from parent process.

        Sends a tool_call message and blocks waiting for a JSON response
        from stdin. Raises RuntimeError if the response contains an error.
        """
        _send({"tool_call": {"name": name, "args": list(args), "kwargs": kwargs}})
        reply = json.loads(input())
        if reply.get("tool_error"):
            raise RuntimeError(reply["tool_error"])
        return reply.get("tool_result")

    def _register_tools(names: list[str]) -> None:
        """Register tool functions in the sandbox globals.

        Creates wrapper functions for each tool name that communicate
        back to the parent process via the JSON protocol.

        Args:
            names: List of tool names to register.
        """
        for name in names:
            if not name.isidentifier() or name in {"SUBMIT"}:
                continue
            if name in sandbox_globals:
                continue

            def _make(name_: str):
                def _fn(*args, **kwargs):
                    return _tool_call(name_, *args, **kwargs)

                return _fn

            sandbox_globals[name] = _make(name)

    def SUBMIT(*args, **kwargs):
        """Return structured final output from sandboxed code.

        This function is injected into the sandbox globals and allows
        executed code to return structured data back to the parent process.

        Args:
            *args: Positional values to return. If output_names was specified
                in the command, args must match the number of output names.
            **kwargs: Keyword arguments to return as a dict.

        Raises:
            _FinalOutput: Always raised with the structured output to
                break out of exec() and return control to the driver.
        """
        if kwargs:
            raise _FinalOutput(kwargs)

        if not output_names:
            if len(args) == 1:
                raise _FinalOutput({"output": args[0]})
            raise _FinalOutput({"output": list(args)})

        if len(args) != len(output_names):
            raise _FinalOutput(
                {
                    "error": f"SUBMIT expected {len(output_names)} positional values ({output_names}), got {len(args)}"
                }
            )

        raise _FinalOutput(dict(zip(output_names, args)))

    sandbox_globals["SUBMIT"] = SUBMIT

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

        if code is None:
            _send({"stdout": "", "stderr": "[Error] No code provided", "final": None})
            continue

        sandbox_globals.update(variables)
        _register_tools(tool_names)

        stdout_io = StringIO()
        stderr_io = StringIO()
        final_obj = None

        with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
            try:
                exec(code, sandbox_globals)
            except _FinalOutput as exc:
                final_obj = exc.args[0] if exc.args else None
            except Exception as exc:  # pragma: no cover
                print(f"[Error] {type(exc).__name__}: {exc}", file=sys.stderr)

        _send(
            {
                "stdout": stdout_io.getvalue(),
                "stderr": stderr_io.getvalue(),
                "final": final_obj,
            }
        )
