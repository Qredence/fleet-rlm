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
        if proto_out is None:
            return
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

    # ------------------------------------------------------------------
    # Sandbox-side helpers
    # ------------------------------------------------------------------
    # These helpers are injected into sandbox_globals so the LLM-generated
    # code can call them directly.  They use only the stdlib (no external
    # deps) because they execute inside the Modal sandbox image.
    # ------------------------------------------------------------------

    def peek(text: str, start: int = 0, length: int = 2000) -> str:
        """Return a slice of *text* starting at *start* for *length* chars.

        Useful for inspecting a portion of a long document without
        exceeding context limits.
        """
        return text[start : start + length]

    sandbox_globals["peek"] = peek

    def grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
        """Return all lines in *text* that contain *pattern* (case-insensitive).

        Args:
            text: The text to search.
            pattern: Substring to match (case-insensitive).
            context: Number of surrounding lines to include (0 = matched line only).

        Returns:
            A list of matching lines (or line groups with context).
        """
        import re as _re

        lines = text.splitlines()
        pat = _re.compile(_re.escape(pattern), _re.IGNORECASE)
        hits: list[str] = []
        for idx, line in enumerate(lines):
            if pat.search(line):
                lo = max(0, idx - context)
                hi = min(len(lines), idx + context + 1)
                hits.append("\n".join(lines[lo:hi]))
        return hits

    sandbox_globals["grep"] = grep

    def chunk_by_size(text: str, size: int = 4000, overlap: int = 200) -> list[str]:
        """Split *text* into fixed-size chunks with optional overlap.

        Args:
            text: Text to chunk.
            size: Maximum characters per chunk.
            overlap: Characters of overlap between consecutive chunks.

        Returns:
            List of text chunks.

        Raises:
            ValueError: If size <= 0, overlap < 0, or overlap >= size.
        """
        if not text:
            return []
        if size <= 0:
            raise ValueError("size must be positive")
        if overlap < 0:
            raise ValueError("overlap must be non-negative")
        if overlap >= size:
            raise ValueError("overlap must be less than size")

        chunks: list[str] = []
        step = size - overlap
        for start in range(0, len(text), step):
            chunk = text[start : start + size]
            if chunk:
                chunks.append(chunk)
            # Stop if we've reached the end
            if start + size >= len(text):
                break
        return chunks

    sandbox_globals["chunk_by_size"] = chunk_by_size

    def chunk_by_headers(
        text: str, pattern: str = r"^#{1,3}\s"
    ) -> list[dict[str, str]]:
        """Split *text* at lines matching *pattern* (regex).

        Args:
            text: Document text.
            pattern: Regex pattern that marks a section header.

        Returns:
            List of dicts ``{"header": ..., "content": ...}`` for each section.
        """
        import re as _re

        parts: list[dict[str, str]] = []
        current_header = ""
        current_lines: list[str] = []
        regex = _re.compile(pattern)
        for line in text.splitlines(keepends=True):
            if regex.match(line):
                if current_lines:
                    parts.append(
                        {
                            "header": current_header.strip(),
                            "content": "".join(current_lines),
                        }
                    )
                current_header = line
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines or current_header:
            parts.append(
                {"header": current_header.strip(), "content": "".join(current_lines)}
            )
        return parts

    sandbox_globals["chunk_by_headers"] = chunk_by_headers

    # ------ Stateful buffers ------
    _buffers: dict[str, list] = {}

    def add_buffer(name: str, value) -> None:
        """Append *value* to the named buffer."""
        _buffers.setdefault(name, []).append(value)

    def get_buffer(name: str) -> list:
        """Return the contents of the named buffer (empty list if missing)."""
        return list(_buffers.get(name, []))

    def clear_buffer(name: str | None = None) -> None:
        """Clear one or all buffers."""
        if name is None:
            _buffers.clear()
        else:
            _buffers.pop(name, None)

    sandbox_globals["add_buffer"] = add_buffer
    sandbox_globals["get_buffer"] = get_buffer
    sandbox_globals["clear_buffer"] = clear_buffer

    # ------ Volume persistence helpers ------

    def save_to_volume(path: str, content: str) -> str:
        """Write *content* to ``/data/<path>`` if volume is mounted.

        Returns the full path written, or an error string.
        """
        import os as _os

        base = "/data"
        if not _os.path.isdir(base):
            return "[no volume mounted at /data]"
        full = _os.path.join(base, path)
        _os.makedirs(_os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
        return full

    def load_from_volume(path: str) -> str:
        """Read text from ``/data/<path>``.

        Returns the file contents, or an error string.
        """
        import os as _os

        full = _os.path.join("/data", path)
        if not _os.path.isfile(full):
            return f"[file not found: {full}]"
        with open(full) as fh:
            return fh.read()

    sandbox_globals["save_to_volume"] = save_to_volume
    sandbox_globals["load_from_volume"] = load_from_volume

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
