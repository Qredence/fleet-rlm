"""Guide-native sandbox-resident driver source for the Daytona-backed RLM pilot."""

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
    import keyword
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
    EXECUTE_EVENT_CHAR_LIMIT = 4000
    EXECUTE_EVENT_CHUNK_SIZE = 400
    REPO_PATH = sys.argv[1]
    FLEET_ROOT = pathlib.Path(REPO_PATH) / ".fleet-rlm"
    PROMPT_ROOT = FLEET_ROOT / "prompts"
    PROMPT_MANIFEST_PATH = PROMPT_ROOT / "manifest.json"
    STATE: dict[str, object] = {
        "__builtins__": __builtins__,
        "json": json,
    }


    def emit(payload: dict[str, object]) -> None:
        sys.__stdout__.write(
            FRAME_PREFIX + json.dumps(payload, ensure_ascii=False, default=repr) + "\n"
        )
        sys.__stdout__.flush()


    def emit_execute_event(
        request_id: str,
        stream: str,
        text: str,
        *,
        truncated: bool = False,
    ) -> None:
        emit(
            {
                "type": "execute_event",
                "request_id": request_id,
                "stream": stream,
                "text": text,
                "truncated": truncated,
            }
        )


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


    def collapse_preview(text: str, limit: int = 240) -> str:
        collapsed = re.sub(r"\s+", " ", str(text)).strip()
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[:limit].rstrip()


    def ensure_prompt_store() -> None:
        PROMPT_ROOT.mkdir(parents=True, exist_ok=True)
        if not PROMPT_MANIFEST_PATH.exists():
            PROMPT_MANIFEST_PATH.write_text(
                json.dumps({"handles": []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


    def load_prompt_manifest() -> dict[str, object]:
        ensure_prompt_store()
        try:
            payload = json.loads(PROMPT_MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {"handles": []}
        handles = payload.get("handles", [])
        if not isinstance(handles, list):
            handles = []
        return {"handles": [item for item in handles if isinstance(item, dict)]}


    def save_prompt_manifest(handles: list[dict[str, object]]) -> None:
        ensure_prompt_store()
        PROMPT_MANIFEST_PATH.write_text(
            json.dumps({"handles": handles}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


    def store_prompt(
        text: str,
        kind: str = "manual",
        label: str | None = None,
    ) -> dict[str, object]:
        ensure_prompt_store()
        normalized_kind = str(kind or "manual").strip() or "manual"
        normalized_label = str(label).strip() if label is not None else ""
        prompt_text = str(text)
        handle_id = f"prompt-{uuid.uuid4().hex}"
        prompt_path = PROMPT_ROOT / f"{handle_id}.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")

        handle = {
            "handle_id": handle_id,
            "kind": normalized_kind,
            "label": normalized_label or None,
            "path": display_path(prompt_path),
            "char_count": len(prompt_text),
            "line_count": len(prompt_text.splitlines()),
            "preview": collapse_preview(prompt_text),
        }
        manifest = load_prompt_manifest()
        handles = list(manifest["handles"])
        handles.append(handle)
        save_prompt_manifest(handles)
        return handle


    def list_prompts() -> dict[str, object]:
        manifest = load_prompt_manifest()
        handles = list(manifest["handles"])
        return {
            "status": "ok",
            "count": len(handles),
            "handles": handles,
        }


    def read_prompt_slice(
        handle_id: str,
        start_line: int = 1,
        num_lines: int = 120,
        start_char: int | None = None,
        char_count: int | None = None,
    ) -> dict[str, object]:
        manifest = load_prompt_manifest()
        handle = next(
            (
                item
                for item in manifest["handles"]
                if str(item.get("handle_id", "")) == str(handle_id)
            ),
            None,
        )
        if handle is None:
            return {
                "status": "error",
                "error": f"Prompt handle not found: {handle_id}",
            }

        handle_path = pathlib.Path(resolve_path(str(handle.get("path", ""))))
        if not handle_path.exists():
            return {
                "status": "error",
                "error": f"Prompt path not found: {display_path(handle_path)}",
                "handle_id": str(handle_id),
            }

        text = handle_path.read_text(encoding="utf-8", errors="replace")
        total_chars = len(text)
        total_lines = len(text.splitlines())

        if start_char is not None:
            start_idx = max(0, int(start_char))
            count = max(0, int(char_count if char_count is not None else 4000))
            slice_text = text[start_idx : start_idx + count]
            end_char = start_idx + len(slice_text)
            return {
                "status": "ok",
                "handle_id": str(handle_id),
                "kind": handle.get("kind"),
                "label": handle.get("label"),
                "path": handle.get("path"),
                "start_char": start_idx,
                "end_char": end_char,
                "total_chars": total_chars,
                "total_lines": total_lines,
                "text": slice_text,
                "preview": collapse_preview(slice_text),
            }

        lines = text.splitlines()
        start_idx = max(0, int(start_line) - 1)
        end_idx = min(len(lines), start_idx + max(0, int(num_lines)))
        slice_lines = lines[start_idx:end_idx]
        slice_text = "\n".join(slice_lines)
        end_line = start_idx + len(slice_lines)
        return {
            "status": "ok",
            "handle_id": str(handle_id),
            "kind": handle.get("kind"),
            "label": handle.get("label"),
            "path": handle.get("path"),
            "start_line": start_idx + 1 if slice_lines else int(start_line),
            "end_line": end_line if slice_lines else int(start_line),
            "total_chars": total_chars,
            "total_lines": total_lines,
            "text": slice_text,
            "preview": collapse_preview(slice_text),
        }


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
    submit_schema: list[dict[str, str | None]] = []

    def normalize_submit_schema(raw: object) -> list[dict[str, str | None]]:
        if not isinstance(raw, list):
            return []

        fields: list[dict[str, str | None]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name or not name.isidentifier() or keyword.iskeyword(name):
                continue
            if name in seen:
                continue
            seen.add(name)
            type_expr = str(item.get("type", "") or "").strip() or None
            fields.append({"name": name, "type": type_expr})
        return fields


    def build_submit_signature(fields: list[dict[str, str | None]]) -> str:
        if not fields:
            return "output=None, **kwargs"

        parts: list[str] = []
        for field in fields:
            name = field["name"]
            type_expr = field.get("type") or "object"
            parts.append(f"{name}: {type_expr} = None")
        parts.append("**kwargs")
        return ", ".join(parts)


    def normalize_submit_value(
        args: tuple[object, ...],
        kwargs: dict[str, object],
        fields: list[dict[str, str | None]],
    ) -> object:
        if kwargs:
            cleaned = {str(key): value for key, value in kwargs.items() if value is not None}
            return cleaned
        if not args:
            return {}
        if fields:
            mapped: dict[str, object] = {}
            for index, field in enumerate(fields):
                if index >= len(args):
                    break
                value = args[index]
                if value is None:
                    continue
                mapped[str(field["name"])] = value
            if mapped:
                return mapped
        if len(args) == 1:
            return args[0]
        return list(args)


    def install_submit(raw_schema: object) -> None:
        global submit_schema
        submit_schema = normalize_submit_schema(raw_schema)
        if not submit_schema:
            source = (
                "def SUBMIT(output=None, **kwargs):\n"
                "    if kwargs:\n"
                "        cleaned = {k: v for k, v in kwargs.items() if v is not None}\n"
                "        return _submit_impl(**cleaned)\n"
                "    if output is not None:\n"
                "        return _submit_impl(output)\n"
                "    return _submit_impl()\n"
            )
        else:
            signature = build_submit_signature(submit_schema)
            source = (
                f"def SUBMIT({signature}):\n"
                "    provided = {k: v for k, v in locals().items() if k not in {'kwargs'} and v is not None}\n"
                "    if kwargs:\n"
                "        provided.update({k: v for k, v in kwargs.items() if v is not None})\n"
                "    if provided:\n"
                "        return _submit_impl(**provided)\n"
                "    if 'output' in locals() and output is not None:\n"
                "        return _submit_impl(output)\n"
                "    return _submit_impl()\n"
            )
        exec(source, STATE, STATE)


    class ProgressCapture(io.TextIOBase):
        def __init__(self, *, request_id: str, stream: str, mirror: io.StringIO) -> None:
            self.request_id = request_id
            self.stream = stream
            self.mirror = mirror
            self.sent_chars = 0
            self.pending = ""
            self.truncated = False

        def writable(self) -> bool:
            return True

        def write(self, data: str) -> int:
            text = str(data or "")
            if not text:
                return 0
            self.mirror.write(text)
            self.pending += text
            self._drain_pending()
            return len(text)

        def flush(self) -> None:
            self._drain_pending(force=True)
            self.mirror.flush()

        def _drain_pending(self, *, force: bool = False) -> None:
            while self.pending:
                if self.sent_chars >= EXECUTE_EVENT_CHAR_LIMIT:
                    self._emit_truncation_notice()
                    self.pending = ""
                    return

                newline_idx = self.pending.find("\n")
                if newline_idx != -1 and newline_idx + 1 <= EXECUTE_EVENT_CHUNK_SIZE:
                    chunk_len = newline_idx + 1
                elif len(self.pending) >= EXECUTE_EVENT_CHUNK_SIZE:
                    chunk_len = EXECUTE_EVENT_CHUNK_SIZE
                elif force:
                    chunk_len = len(self.pending)
                else:
                    return

                chunk = self.pending[:chunk_len]
                self.pending = self.pending[chunk_len:]
                remaining = EXECUTE_EVENT_CHAR_LIMIT - self.sent_chars
                if remaining <= 0:
                    self._emit_truncation_notice()
                    self.pending = ""
                    return
                emitted = chunk[:remaining]
                if emitted:
                    self.sent_chars += len(emitted)
                    emit_execute_event(self.request_id, self.stream, emitted)
                if len(chunk) > remaining:
                    self._emit_truncation_notice()
                    self.pending = ""
                    return

        def _emit_truncation_notice(self) -> None:
            if self.truncated:
                return
            emit_execute_event(
                self.request_id,
                self.stream,
                f"[{self.stream} output truncated after {EXECUTE_EVENT_CHAR_LIMIT} chars]",
                truncated=True,
            )
            self.truncated = True


    def _submit_impl(
        *args: object,
        _variable_name: str | None = None,
        **kwargs: object,
    ) -> object:
        global final_artifact
        value = normalize_submit_value(args, kwargs, submit_schema)
        final_artifact = {
            "kind": "markdown",
            "value": value,
            "variable_name": _variable_name,
            "finalization_mode": "SUBMIT",
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


    def llm_query(task: object) -> object:
        return request_host_callback("llm_query", {"task": task})


    def llm_query_batched(tasks: list[object]) -> object:
        return request_host_callback("llm_query_batched", {"tasks": tasks})


    def rlm_query(task: object) -> object:
        return request_host_callback("rlm_query", {"task": task})


    def rlm_query_batched(tasks: list[object]) -> object:
        return request_host_callback("rlm_query_batched", {"tasks": tasks})


    STATE.update(
        {
            "run": run,
            "read_file": read_file,
            "store_prompt": store_prompt,
            "list_prompts": list_prompts,
            "read_prompt_slice": read_prompt_slice,
            "read_file_slice": read_file_slice,
            "list_files": list_files,
            "find_files": find_files,
            "grep_repo": grep_repo,
            "chunk_text": chunk_text,
            "chunk_file": chunk_file,
            "llm_query": llm_query,
            "llm_query_batched": llm_query_batched,
            "rlm_query": rlm_query,
            "rlm_query_batched": rlm_query_batched,
            "_submit_impl": _submit_impl,
        }
    )
    install_submit(None)

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
        install_submit(message.get("submit_schema"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        progress_stdout = ProgressCapture(
            request_id=str(message["request_id"]),
            stream="stdout",
            mirror=stdout,
        )
        progress_stderr = ProgressCapture(
            request_id=str(message["request_id"]),
            stream="stderr",
            mirror=stderr,
        )
        error_text: str | None = None
        started = time.perf_counter()
        with contextlib.redirect_stdout(progress_stdout), contextlib.redirect_stderr(progress_stderr):
            try:
                exec(str(message["code"]), STATE, STATE)
            except Exception:
                error_text = traceback.format_exc(limit=8)
        progress_stdout.flush()
        progress_stderr.flush()
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
