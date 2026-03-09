"""Sandbox-resident driver source for the Daytona-backed RLM pilot."""

from __future__ import annotations

from textwrap import dedent

DAYTONA_DRIVER_SOURCE = (
    dedent(
        r"""
    from __future__ import annotations

    import contextlib
    import fnmatch
    import glob
    import io
    import json
    import os
    import pathlib
    import re
    import shutil
    import subprocess
    import sys
    import time
    import traceback
    import uuid

    FRAME_PREFIX = "__fleet_rlm_daytona__:"
    REPO_PATH = sys.argv[1]
    STATE: dict[str, object] = {
        "__builtins__": __builtins__,
        "json": json,
    }


    def emit(payload: dict[str, object]) -> None:
        sys.__stdout__.write(
            FRAME_PREFIX + json.dumps(payload, ensure_ascii=False, default=repr) + "\n"
        )
        sys.__stdout__.flush()


    def parse_input(raw: str) -> dict[str, object]:
        payload = raw.strip()
        if payload.startswith(FRAME_PREFIX):
            payload = payload[len(FRAME_PREFIX) :]
        return json.loads(payload)


    def resolve_path(path: str) -> str:
        candidate = pathlib.Path(path)
        if candidate.is_absolute():
            return str(candidate)
        return str(pathlib.Path(REPO_PATH) / candidate)


    def run(command: str, cwd: str | None = None) -> dict[str, object]:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=resolve_path(cwd) if cwd else REPO_PATH,
            capture_output=True,
            text=True,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "ok": completed.returncode == 0,
        }


    def read_file(path: str) -> str:
        with open(resolve_path(path), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()


    def list_files(path: str = ".") -> list[str]:
        target = pathlib.Path(resolve_path(path))
        if not target.exists():
            return []
        return sorted(str(item) for item in target.iterdir())


    def find_files(path: str = ".", pattern: str = "*") -> list[str]:
        target = pathlib.Path(resolve_path(path))
        if not target.exists():
            return []
        return sorted(glob.glob(str(target / pattern), recursive=True))


    def display_path(path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(REPO_PATH))
        except ValueError:
            return str(path)


    def read_file_slice(
        path: str,
        start_line: int = 1,
        num_lines: int = 100,
    ) -> dict[str, object]:
        target = pathlib.Path(resolve_path(path))
        if not target.exists():
            return {"status": "error", "error": f"File not found: {display_path(target)}"}
        if target.is_dir():
            return {
                "status": "error",
                "error": f"Cannot read lines from directory: {display_path(target)}",
            }

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + max(0, num_lines))
        slice_lines = lines[start_idx:end_idx]
        numbered = [
            {"line": start_idx + index + 1, "text": text}
            for index, text in enumerate(slice_lines)
        ]

        return {
            "status": "ok",
            "path": display_path(target),
            "start_line": start_line,
            "lines": numbered,
            "returned_count": len(numbered),
            "total_lines": total_lines,
        }


    def _iter_search_files(target: pathlib.Path):
        ignored_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".tmpl",
            ".next",
            "dist",
            "build",
            ".dspy",
            ".mypy_cache",
            ".pytest_cache",
            "__target__",
        }

        if target.is_file():
            yield target
            return

        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [name for name in dirnames if name not in ignored_dirs]
            for filename in filenames:
                yield pathlib.Path(dirpath) / filename


    def _grep_repo_with_rg(
        pattern: str,
        target: pathlib.Path,
        include: str,
    ) -> dict[str, object] | None:
        rg_path = shutil.which("rg")
        if rg_path is None:
            return None

        search_arg = (
            display_path(target)
            if target.is_absolute()
            else str(target)
        )
        if not search_arg:
            search_arg = "."
        args = [
            rg_path,
            "--json",
            "--line-number",
            "--with-filename",
            "--max-count",
            "50",
        ]
        if include:
            args.extend(["--glob", include])
        args.extend([pattern, search_arg])
        completed = subprocess.run(
            args,
            cwd=REPO_PATH,
            capture_output=True,
            text=True,
        )
        if completed.returncode not in {0, 1}:
            return {
                "status": "error",
                "pattern": pattern,
                "search_path": search_arg,
                "include": include or "all files",
                "error": (completed.stderr or completed.stdout or "ripgrep failed").strip(),
            }

        hits: list[dict[str, object]] = []
        for raw in completed.stdout.splitlines():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "match":
                continue
            data = payload.get("data", {})
            path_text = str(data.get("path", {}).get("text", "") or "")
            line_no = int(data.get("line_number", 0) or 0)
            line_text = str(data.get("lines", {}).get("text", "") or "").rstrip("\n")
            hits.append({"path": path_text, "line": line_no, "text": line_text})

        return {
            "status": "ok",
            "pattern": pattern,
            "search_path": search_arg,
            "include": include or "all files",
            "count": len(hits),
            "hits": hits[:20],
        }


    def grep_repo(
        pattern: str,
        path: str = ".",
        include: str = "",
    ) -> dict[str, object]:
        target = pathlib.Path(resolve_path(path))
        if not target.exists():
            return {"status": "error", "error": f"Path not found: {display_path(target)}"}

        rg_result = _grep_repo_with_rg(pattern=pattern, target=target, include=include)
        if rg_result is not None:
            return rg_result

        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return {
                "status": "error",
                "pattern": pattern,
                "search_path": display_path(target),
                "include": include or "all files",
                "error": f"Invalid regex: {exc}",
            }

        hits: list[dict[str, object]] = []
        for candidate in _iter_search_files(target):
            relative_path = display_path(candidate)
            if include and not fnmatch.fnmatch(relative_path, include):
                continue
            try:
                lines = candidate.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except Exception as exc:
                return {
                    "status": "error",
                    "pattern": pattern,
                    "search_path": display_path(target),
                    "include": include or "all files",
                    "error": f"Failed reading {relative_path}: {exc}",
                }
            for index, line in enumerate(lines, start=1):
                if compiled.search(line):
                    hits.append({"path": relative_path, "line": index, "text": line})
                    if len(hits) >= 50:
                        return {
                            "status": "ok",
                            "pattern": pattern,
                            "search_path": display_path(target),
                            "include": include or "all files",
                            "count": len(hits),
                            "hits": hits[:20],
                        }

        return {
            "status": "ok",
            "pattern": pattern,
            "search_path": display_path(target),
            "include": include or "all files",
            "count": len(hits),
            "hits": hits[:20],
        }


    def chunk_by_size(text: str, size: int = 200_000, overlap: int = 0) -> list[str]:
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


    def chunk_by_headers(
        text: str,
        pattern: str = r"^#{1,3} ",
        flags: int = re.MULTILINE,
    ) -> list[dict[str, object]]:
        if not text:
            return []

        compiled = re.compile(pattern, flags | re.MULTILINE)
        matches = list(compiled.finditer(text))
        if not matches:
            return [{"header": "", "content": text.strip(), "start_pos": 0}]

        parts: list[dict[str, object]] = []
        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                parts.append({"header": "", "content": preamble, "start_pos": 0})

        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
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


    def chunk_by_timestamps(
        text: str,
        pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]",
        flags: int = re.MULTILINE,
    ) -> list[dict[str, object]]:
        if not text:
            return []

        compiled = re.compile(pattern, flags)
        matches = list(compiled.finditer(text))
        if not matches:
            return [{"timestamp": "", "content": text, "start_pos": 0}]

        chunks: list[dict[str, object]] = []
        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                chunks.append({"timestamp": "", "content": preamble, "start_pos": 0})

        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.start() : end].strip()
            chunks.append(
                {
                    "timestamp": match.group(0).strip(),
                    "content": content,
                    "start_pos": match.start(),
                }
            )
        return chunks


    def chunk_by_json_keys(text: str) -> list[dict[str, object]]:
        if not text or not text.strip():
            return []

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        chunks: list[dict[str, object]] = []
        for key, value in data.items():
            chunks.append(
                {
                    "key": key,
                    "content": json.dumps(value, indent=2, default=str),
                    "value_type": type(value).__name__,
                }
            )
        return chunks


    def chunk_text(
        text: str,
        strategy: str = "size",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> list[object]:
        strategy_norm = (strategy or "size").strip().lower()
        if strategy_norm == "size":
            return chunk_by_size(text, size=size, overlap=overlap)
        if strategy_norm == "headers":
            return chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
        if strategy_norm == "timestamps":
            return chunk_by_timestamps(text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]")
        if strategy_norm in {"json_keys", "json-keys"}:
            return chunk_by_json_keys(text)
        raise ValueError(f"Unknown chunking strategy: {strategy}")


    def chunk_file(
        path: str,
        strategy: str = "size",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, object]:
        target = pathlib.Path(resolve_path(path))
        if not target.exists():
            return {"status": "error", "error": f"File not found: {display_path(target)}"}
        if target.is_dir():
            return {
                "status": "error",
                "error": f"Cannot chunk directory: {display_path(target)}",
            }

        try:
            text = read_file(path)
            chunks = chunk_text(
                text,
                strategy=strategy,
                size=size,
                overlap=overlap,
                pattern=pattern,
            )
        except Exception as exc:
            return {
                "status": "error",
                "path": display_path(target),
                "strategy": strategy,
                "error": str(exc),
            }

        preview = chunks[0] if chunks else ""
        return {
            "status": "ok",
            "path": display_path(target),
            "strategy": strategy,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "preview": preview,
        }


    final_artifact: dict[str, object] | None = None
    callback_count = 0


    def FINAL(value: object) -> object:
        global final_artifact
        final_artifact = {
            "kind": "markdown",
            "value": value,
            "finalization_mode": "FINAL",
        }
        return value


    def FINAL_VAR(variable_name: str) -> object:
        global final_artifact
        if variable_name not in STATE:
            raise NameError(f"Variable '{variable_name}' is not defined")
        value = STATE[variable_name]
        final_artifact = {
            "kind": "markdown",
            "value": value,
            "variable_name": variable_name,
            "finalization_mode": "FINAL_VAR",
        }
        return value


    def request_host_callback(name: str, payload: dict[str, object]) -> object:
        global callback_count
        callback_count += 1
        callback_id = uuid.uuid4().hex
        emit(
            {
                "type": "host_callback_request",
                "callback_id": callback_id,
                "name": name,
                "payload": payload,
            }
        )
        while True:
            raw = sys.stdin.readline()
            if not raw:
                raise RuntimeError("Host disconnected while waiting for callback result")
            message = parse_input(raw)
            if message.get("type") != "host_callback_response":
                continue
            if message.get("callback_id") != callback_id:
                continue
            if not message.get("ok", False):
                raise RuntimeError(message.get("error") or "Host callback failed")
            return message.get("value")


    def rlm_query(task: str) -> object:
        return request_host_callback("rlm_query", {"task": task})


    def rlm_query_batched(tasks: list[str]) -> object:
        return request_host_callback("rlm_query_batched", {"tasks": tasks})


    STATE.update(
        {
            "run": run,
            "read_file": read_file,
            "read_file_slice": read_file_slice,
            "list_files": list_files,
            "find_files": find_files,
            "grep_repo": grep_repo,
            "chunk_text": chunk_text,
            "chunk_file": chunk_file,
            "rlm_query": rlm_query,
            "rlm_query_batched": rlm_query_batched,
            "FINAL": FINAL,
            "FINAL_VAR": FINAL_VAR,
        }
    )

    emit({"type": "driver_ready", "message": "ready"})

    for raw in sys.stdin:
        message = parse_input(raw)
        msg_type = message.get("type")
        if msg_type == "shutdown":
            emit({"type": "shutdown_ack"})
            break
        if msg_type != "execute_request":
            continue

        final_artifact = None
        callback_count = 0
        stdout = io.StringIO()
        stderr = io.StringIO()
        error_text: str | None = None
        started = time.perf_counter()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(str(message["code"]), STATE, STATE)
            except Exception:
                error_text = traceback.format_exc(limit=8)
        duration_ms = int((time.perf_counter() - started) * 1000)
        emit(
            {
                "type": "execute_response",
                "request_id": message["request_id"],
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "error": error_text,
                "final_artifact": final_artifact,
                "duration_ms": duration_ms,
                "callback_count": callback_count,
            }
        )
    """
    ).strip()
    + "\n"
)
