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
            "execution_profile": "ROOT_INTERLOCUTOR|RLM_DELEGATE|MAINTENANCE"  // Optional
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

    import json
    import re as _re
    import sys
    from contextlib import redirect_stderr, redirect_stdout
    from io import StringIO
    from typing import Any

    # Persistent globals that survive across code execution calls
    sandbox_globals: dict[str, Any] = {}
    proto_out = sys.__stdout__
    current_execution_profile = "RLM_DELEGATE"
    _dynamic_tool_names: set[str] = set()

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

    # Reserved tool names that conflict with built-in sandbox functions
    _RESERVED_TOOL_NAMES = frozenset(
        {"llm_query", "llm_query_batched", "SUBMIT", "print"}
    )

    def _wrap_helper(fn):
        """Guard helper availability by execution profile."""

        def _wrapped(*args, **kwargs):
            if current_execution_profile == "ROOT_INTERLOCUTOR":
                raise RuntimeError(
                    f"Helper '{fn.__name__}' is not available in ROOT_INTERLOCUTOR profile. "
                    "Delegate tool-heavy work via llm_query/llm_query_batched."
                )
            return fn(*args, **kwargs)

        _wrapped.__name__ = fn.__name__
        _wrapped.__doc__ = fn.__doc__
        return _wrapped

    def _register_tools(names: list[str]) -> None:
        """Register tool functions in the sandbox globals.

        Creates wrapper functions for each tool name that communicate
        back to the parent process via the JSON protocol.

        Args:
            names: List of tool names to register.
        """
        if current_execution_profile == "ROOT_INTERLOCUTOR":
            for dyn_name in list(_dynamic_tool_names):
                sandbox_globals.pop(dyn_name, None)
            _dynamic_tool_names.clear()
            return

        for name in names:
            if not name.isidentifier() or name in _RESERVED_TOOL_NAMES:
                continue
            if name in sandbox_globals:
                continue

            def _make(name_: str):
                def _fn(*args, **kwargs):
                    return _tool_call(name_, *args, **kwargs)

                return _fn

            sandbox_globals[name] = _make(name)
            _dynamic_tool_names.add(name)

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
    # Built-in RLM tools: llm_query and llm_query_batched
    # ------------------------------------------------------------------
    # These tools enable recursive LLM calls from within sandboxed code.
    # They use _tool_call() to communicate back to the parent interpreter
    # which handles the actual LLM queries and call counting.
    # ------------------------------------------------------------------

    def llm_query(prompt: str) -> str:
        """Query a sub-LLM for semantic analysis.

        Args:
            prompt: The prompt to send to the sub-LLM.

        Returns:
            The response text from the sub-LLM.

        Raises:
            RuntimeError: If the LLM call fails or max_llm_calls exceeded.
        """
        return _tool_call("llm_query", prompt)

    def llm_query_batched(prompts: list[str]) -> list[str]:
        """Query the sub-LLM with multiple prompts concurrently.

        Args:
            prompts: List of prompts to send to the sub-LLM.

        Returns:
            List of response texts, in the same order as prompts.

        Raises:
            RuntimeError: If any LLM call fails or max_llm_calls exceeded.
        """
        return _tool_call("llm_query_batched", prompts)

    sandbox_globals["llm_query"] = llm_query
    sandbox_globals["llm_query_batched"] = llm_query_batched

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

    sandbox_globals["peek"] = _wrap_helper(peek)

    def grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
        """Return all lines in *text* that contain *pattern* (case-insensitive).

        Args:
            text: The text to search.
            pattern: Substring to match (case-insensitive).
            context: Number of surrounding lines to include (0 = matched line only).

        Returns:
            A list of matching lines (or line groups with context).
        """
        lines = text.splitlines()
        pat = _re.compile(_re.escape(pattern), _re.IGNORECASE)
        hits: list[str] = []
        for idx, line in enumerate(lines):
            if pat.search(line):
                lo = max(0, idx - context)
                hi = min(len(lines), idx + context + 1)
                hits.append("\n".join(lines[lo:hi]))
        return hits

    sandbox_globals["grep"] = _wrap_helper(grep)

    # NOTE: These functions mirror fleet_rlm.chunking (the canonical source).
    # Keep defaults and logic in sync with chunking.py.
    def chunk_by_size(text: str, size: int = 200_000, overlap: int = 0) -> list[str]:
        """Split *text* into fixed-size chunks with optional overlap."""
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
            if start + size >= len(text):
                break
        return chunks

    sandbox_globals["chunk_by_size"] = _wrap_helper(chunk_by_size)

    def chunk_by_headers(
        text: str,
        pattern: str = r"^#{1,3} ",
        flags: int = _re.MULTILINE,
    ) -> list[dict]:
        """Split *text* at lines matching *pattern* (regex)."""
        if not text:
            return []

        compiled = _re.compile(pattern, flags | _re.MULTILINE)
        matches = list(compiled.finditer(text))

        if not matches:
            return [{"header": "", "content": text.strip(), "start_pos": 0}]

        parts: list[dict] = []

        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                parts.append({"header": "", "content": preamble, "start_pos": 0})

        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section = text[match.start() : end]

            newline_pos = section.find("\n")
            if newline_pos == -1:
                header = section.strip()
                content = ""
            else:
                header = section[:newline_pos].strip()
                content = section[newline_pos + 1 :].strip()

            parts.append(
                {"header": header, "content": content, "start_pos": match.start()}
            )
        return parts

    sandbox_globals["chunk_by_headers"] = _wrap_helper(chunk_by_headers)

    def chunk_by_timestamps(
        text: str,
        pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]",
        flags: int = _re.MULTILINE,
    ) -> list[dict]:
        """Split log-style text by timestamp boundaries."""
        if not text:
            return []

        compiled = _re.compile(pattern, flags)
        matches = list(compiled.finditer(text))

        if not matches:
            return [{"timestamp": "", "content": text, "start_pos": 0}]

        chunks: list[dict] = []

        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                chunks.append({"timestamp": "", "content": preamble, "start_pos": 0})

        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[match.start() : end].strip()
            timestamp = match.group(0).strip()
            chunks.append(
                {
                    "timestamp": timestamp,
                    "content": content,
                    "start_pos": match.start(),
                }
            )

        return chunks

    sandbox_globals["chunk_by_timestamps"] = _wrap_helper(chunk_by_timestamps)

    def chunk_by_json_keys(text: str) -> list[dict]:
        """Split a JSON object into per-key chunks."""
        if not text or not text.strip():
            return []

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        chunks: list[dict] = []
        for key, value in data.items():
            chunks.append(
                {
                    "key": key,
                    "content": json.dumps(value, indent=2, default=str),
                    "value_type": type(value).__name__,
                }
            )
        return chunks

    sandbox_globals["chunk_by_json_keys"] = _wrap_helper(chunk_by_json_keys)

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

    sandbox_globals["add_buffer"] = _wrap_helper(add_buffer)
    sandbox_globals["get_buffer"] = _wrap_helper(get_buffer)
    sandbox_globals["clear_buffer"] = _wrap_helper(clear_buffer)

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
        with open(full, "w", encoding="utf-8") as fh:
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
        with open(full, encoding="utf-8") as fh:
            return fh.read()

    sandbox_globals["save_to_volume"] = _wrap_helper(save_to_volume)
    sandbox_globals["load_from_volume"] = _wrap_helper(load_from_volume)

    # ------ Workspace helpers for stateful agent sessions ------

    def _resolve_workspace_path(path: str) -> tuple[str | None, str | None]:
        """Resolve and validate a workspace path stays under /data/workspace."""
        import os as _os

        base = "/data/workspace"
        base_real = _os.path.realpath(base)
        raw = str(path or "").strip()
        if not raw:
            return None, "[error: workspace path cannot be empty]"

        resolved = _os.path.realpath(_os.path.normpath(_os.path.join(base, raw)))
        if resolved != base_real and not resolved.startswith(base_real + _os.sep):
            return None, f"[error: invalid workspace path: {raw}]"

        return resolved, None

    def workspace_write(path: str, content: str) -> str:
        """Write *content* to ``/data/workspace/<path>``.

        Creates parent directories if needed. Returns full path or error.
        """
        import os as _os

        full, path_error = _resolve_workspace_path(path)
        if path_error is not None:
            return path_error

        base = "/data/workspace"
        if not _os.path.isdir("/data"):
            return "[error: no volume mounted at /data]"
        _os.makedirs(base, exist_ok=True)
        if full is None:
            return "[error: invalid workspace path]"
        _os.makedirs(_os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return full

    def workspace_read(path: str) -> str:
        """Read text from ``/data/workspace/<path>``.

        Returns file contents or error message.
        """
        import os as _os

        full, path_error = _resolve_workspace_path(path)
        if path_error is not None:
            return path_error
        if full is None:
            return "[error: invalid workspace path]"
        if not _os.path.isfile(full):
            return f"[error: file not found: {full}]"
        with open(full, encoding="utf-8") as fh:
            return fh.read()

    def workspace_list(pattern: str = "*") -> list[str]:
        """List files in workspace matching glob *pattern*."""
        import glob as _glob
        import os as _os

        base = "/data/workspace"
        if not _os.path.isdir(base):
            return []
        search_path = _os.path.join(base, "**", pattern)
        files = _glob.glob(search_path, recursive=True)
        base_real = _os.path.realpath(base)

        rel_paths: list[str] = []
        for found in files:
            if not _os.path.isfile(found):
                continue
            found_real = _os.path.realpath(found)
            if found_real != base_real and not found_real.startswith(
                base_real + _os.sep
            ):
                continue
            rel_paths.append(_os.fsdecode(_os.path.relpath(found_real, base_real)))
        return rel_paths

    def workspace_append(path: str, content: str) -> str:
        """Append *content* to ``/data/workspace/<path>`` (creates if missing)."""
        import os as _os

        full, path_error = _resolve_workspace_path(path)
        if path_error is not None:
            return path_error

        base = "/data/workspace"
        if not _os.path.isdir("/data"):
            return "[error: no volume mounted at /data]"
        _os.makedirs(base, exist_ok=True)
        if full is None:
            return "[error: invalid workspace path]"
        _os.makedirs(_os.path.dirname(full) or base, exist_ok=True)
        with open(full, "a", encoding="utf-8") as fh:
            fh.write(content)
        return full

    sandbox_globals["workspace_write"] = _wrap_helper(workspace_write)
    sandbox_globals["workspace_read"] = _wrap_helper(workspace_read)
    sandbox_globals["workspace_list"] = _wrap_helper(workspace_list)
    sandbox_globals["workspace_append"] = _wrap_helper(workspace_append)

    # ------ Session execution history ------

    _session_history: list[dict] = []

    def log_execution(code: str, result: dict, metadata: dict | None = None) -> None:
        """Log code execution to session history for tracking and learning."""
        import time as _time

        entry = {
            "timestamp": _time.time(),
            "code_preview": code[:200] + "..." if len(code) > 200 else code,
            "stdout_preview": result.get("stdout", "")[:200],
            "stderr_preview": result.get("stderr", "")[:200],
            "had_final": result.get("final") is not None,
            "metadata": metadata or {},
        }
        _session_history.append(entry)

    def get_session_history() -> list[dict]:
        """Return all logged executions in this session."""
        return list(_session_history)

    def get_last_execution() -> dict | None:
        """Return the most recent execution entry, or None if empty."""
        return _session_history[-1] if _session_history else None

    sandbox_globals["log_execution"] = _wrap_helper(log_execution)
    sandbox_globals["get_session_history"] = _wrap_helper(get_session_history)
    sandbox_globals["get_last_execution"] = _wrap_helper(get_last_execution)

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
            "RLM_DELEGATE",
            "MAINTENANCE",
        }:
            execution_profile = "RLM_DELEGATE"
        current_execution_profile = execution_profile

        if code is None:
            _send({"stdout": "", "stderr": "[Error] No code provided", "final": None})
            continue

        sandbox_globals.update(variables)
        _register_tools(tool_names)

        stdout_io = StringIO()
        stderr_io = StringIO()
        final_obj = None

        had_exec_error = False
        with redirect_stdout(stdout_io), redirect_stderr(stderr_io):
            try:
                exec(code, sandbox_globals)
            except _FinalOutput as exc:
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
