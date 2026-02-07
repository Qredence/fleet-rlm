from __future__ import annotations


def sandbox_driver() -> None:
    """Long-lived JSON protocol driver for Modal Sandbox execution."""

    import json
    import sys
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from typing import Any

    sandbox_globals: dict[str, Any] = {}
    proto_out = sys.__stdout__

    output_names: list[str] = []

    class _FinalOutput(BaseException):
        pass

    def _send(obj: dict) -> None:
        proto_out.write(json.dumps(obj) + "\n")
        proto_out.flush()

    def _tool_call(name: str, *args, **kwargs):
        _send({"tool_call": {"name": name, "args": list(args), "kwargs": kwargs}})
        reply = json.loads(input())
        if reply.get("tool_error"):
            raise RuntimeError(reply["tool_error"])
        return reply.get("tool_result")

    def _register_tools(names: list[str]) -> None:
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
