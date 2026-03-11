"""Sandbox-self-orchestrated Daytona runtime used by the experimental pilot."""

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
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

import dspy
from daytona import Daytona, DaytonaConfig, SessionExecuteRequest

from .config import resolve_daytona_config
from .protocol import (
    RunCancelRequest,
    RunErrorEnvelope,
    RunEventFrame,
    RunReady,
    RunResultEnvelope,
    RunStartRequest,
    decode_frame,
    encode_frame,
)
from .system_prompt import build_system_prompt, build_user_prompt
from .types import (
    AgentNode,
    ChildLink,
    ChildTaskResult,
    DaytonaRunResult,
    DaytonaRunCancelled,
    ExecutionObservation,
    FinalArtifact,
    PromptHandle,
    PromptManifest,
    RecursiveTaskSpec,
    RolloutBudget,
    RolloutSummary,
    SandboxLmRuntimeConfig,
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_PATH_LINE_RE = re.compile(r"^(?:/|\.{1,2}/|[A-Za-z]:\\|[A-Za-z0-9._-]+/).*$")
_GREP_LINE_RE = re.compile(r"^[^:\n]+:\d+(?::\d+)?(?:\s*(?:-|\|)\s*.*|: .*)?$")
_INLINE_PROMPT_LIMIT = 4_000
_ROOT_MIN_CHARS = 80
_ROOT_MIN_WORDS = 12
_WORD_RE = re.compile(r"\b\w+\b")
_GUIDE_SUBMIT_SCHEMA = [
    {"name": "summary", "type": "str | None"},
    {"name": "final_markdown", "type": "str | None"},
    {"name": "output", "type": "object"},
]
_IGNORED_DIRS = {
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
_GLOBAL_CANCEL_WATCHER: "_CancelWatcher | None" = None


def _emit(payload: dict[str, Any]) -> None:
    sys.__stdout__.write(encode_frame(payload) + "\n")
    sys.__stdout__.flush()


def _collapse_preview(text: str, *, limit: int = 240) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip()


def _collapse_plain_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def _preview_text(text: str, *, limit: int = 1_200) -> str:
    stripped = str(text).strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "\n\n[truncated preview]"


def _coerce_lm_output(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"])
        return str(first)
    return str(response)


def _safe_repo_name(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-")
    return cleaned or "repo"


def _looks_like_commit(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref.strip()))


def _display_path(path: pathlib.Path, repo_path: str) -> str:
    try:
        return str(path.relative_to(repo_path))
    except ValueError:
        return str(path)


def _ensure_prompt_store(prompt_root: pathlib.Path) -> pathlib.Path:
    prompt_root.mkdir(parents=True, exist_ok=True)
    manifest_path = prompt_root / "manifest.json"
    if not manifest_path.exists():
        manifest_path.write_text(
            json.dumps({"handles": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return manifest_path


def _load_prompt_manifest(prompt_root: pathlib.Path) -> PromptManifest:
    manifest_path = _ensure_prompt_store(prompt_root)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {"handles": []}
    return PromptManifest.from_raw(payload)


def _save_prompt_manifest(prompt_root: pathlib.Path, manifest: PromptManifest) -> None:
    manifest_path = _ensure_prompt_store(prompt_root)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _iter_search_files(target: pathlib.Path):
    if target.is_file():
        yield target
        return
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS]
        for filename in filenames:
            yield pathlib.Path(dirpath) / filename


def _grep_repo_with_rg(
    *,
    pattern: str,
    target: pathlib.Path,
    include: str,
    repo_path: str,
) -> dict[str, object] | None:
    rg_path = shutil.which("rg")
    if rg_path is None:
        return None
    search_arg = (
        _display_path(target, repo_path) if target.is_absolute() else str(target)
    )
    if not search_arg:
        search_arg = "."
    args = [rg_path, "--json", "--line-number", "--with-filename", "--max-count", "50"]
    if include:
        args.extend(["--glob", include])
    args.extend([pattern, search_arg])
    completed = subprocess.run(args, cwd=repo_path, capture_output=True, text=True)
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
        hits.append(
            {
                "path": str(data.get("path", {}).get("text", "") or ""),
                "line": int(data.get("line_number", 0) or 0),
                "text": str(data.get("lines", {}).get("text", "") or "").rstrip("\n"),
            }
        )
    return {
        "status": "ok",
        "pattern": pattern,
        "search_path": search_arg,
        "include": include or "all files",
        "count": len(hits),
        "hits": hits[:20],
    }


def _chunk_by_size(text: str, *, size: int = 200_000, overlap: int = 0) -> list[str]:
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


def _chunk_by_headers(
    text: str, *, pattern: str = r"^#{1,3} ", flags: int = re.MULTILINE
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
        header = section[:newline_pos].strip() if newline_pos != -1 else section.strip()
        content = section[newline_pos + 1 :].strip() if newline_pos != -1 else ""
        parts.append({"header": header, "content": content, "start_pos": match.start()})
    return parts


def _chunk_by_timestamps(
    text: str, *, pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]", flags: int = re.MULTILINE
) -> list[dict[str, object]]:
    if not text:
        return []
    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))
    if not matches:
        return [{"timestamp": "", "content": text, "start_pos": 0}]
    parts: list[dict[str, object]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            parts.append({"timestamp": "", "content": preamble, "start_pos": 0})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        parts.append(
            {
                "timestamp": match.group(0).strip(),
                "content": content,
                "start_pos": match.start(),
            }
        )
    return parts


def _chunk_by_json_keys(text: str) -> list[dict[str, object]]:
    if not text or not text.strip():
        return []
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return [
        {
            "key": key,
            "content": json.dumps(value, indent=2, default=str),
            "value_type": type(value).__name__,
        }
        for key, value in data.items()
    ]


class SandboxExecutionKernel:
    """Persistent Python execution environment for one Daytona node."""

    def __init__(
        self,
        *,
        repo_path: str,
        recurse_one: Callable[[object], str],
        recurse_many: Callable[[list[object]], list[str]],
    ) -> None:
        self.repo_path = repo_path
        self.prompt_root = pathlib.Path(repo_path) / ".fleet-rlm" / "prompts"
        self.recurse_one = recurse_one
        self.recurse_many = recurse_many
        self.final_artifact: dict[str, object] | None = None
        self.callback_count = 0
        self.submit_schema: list[dict[str, str | None]] = []
        self.state: dict[str, object] = {
            "__builtins__": __builtins__,
            "json": json,
        }
        self._install_helpers()

    def _resolve_path(self, path: str) -> pathlib.Path:
        candidate = pathlib.Path(path)
        if candidate.is_absolute():
            return candidate
        return pathlib.Path(self.repo_path) / candidate

    def run(self, command: str, cwd: str | None = None) -> dict[str, object]:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(self._resolve_path(cwd)) if cwd else self.repo_path,
            capture_output=True,
            text=True,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "ok": completed.returncode == 0,
        }

    def read_file(self, path: str) -> str:
        target = self._resolve_path(path)
        return target.read_text(encoding="utf-8", errors="replace")

    def list_files(self, path: str = ".") -> list[str]:
        target = self._resolve_path(path)
        if not target.exists():
            return []
        return sorted(_display_path(item, self.repo_path) for item in target.iterdir())

    def find_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        target = self._resolve_path(path)
        if not target.exists():
            return []
        return sorted(
            _display_path(pathlib.Path(item), self.repo_path)
            for item in glob.glob(str(target / pattern), recursive=True)
        )

    def store_prompt(
        self, text: str, kind: str = "manual", label: str | None = None
    ) -> dict[str, object]:
        manifest = _load_prompt_manifest(self.prompt_root)
        handle_id = f"prompt-{uuid.uuid4().hex}"
        prompt_path = self.prompt_root / f"{handle_id}.txt"
        prompt_text = str(text)
        prompt_path.write_text(prompt_text, encoding="utf-8")
        handle = PromptHandle(
            handle_id=handle_id,
            kind=str(kind or "manual").strip() or "manual",
            label=(str(label).strip() or None) if label is not None else None,
            path=_display_path(prompt_path, self.repo_path),
            char_count=len(prompt_text),
            line_count=len(prompt_text.splitlines()),
            preview=_collapse_preview(prompt_text),
        )
        manifest.handles.append(handle)
        _save_prompt_manifest(self.prompt_root, manifest)
        return handle.to_dict()

    def list_prompts(self) -> dict[str, object]:
        manifest = _load_prompt_manifest(self.prompt_root)
        return {
            "status": "ok",
            "count": len(manifest.handles),
            "handles": [item.to_dict() for item in manifest.handles],
        }

    def read_prompt_slice(
        self,
        handle_id: str,
        start_line: int = 1,
        num_lines: int = 120,
        start_char: int | None = None,
        char_count: int | None = None,
    ) -> dict[str, object]:
        manifest = _load_prompt_manifest(self.prompt_root)
        handle = next(
            (item for item in manifest.handles if item.handle_id == handle_id), None
        )
        if handle is None:
            return {"status": "error", "error": f"Prompt handle not found: {handle_id}"}
        handle_path = self._resolve_path(handle.path)
        if not handle_path.exists():
            return {
                "status": "error",
                "error": f"Prompt path not found: {handle.path}",
                "handle_id": handle_id,
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
                "handle_id": handle_id,
                "kind": handle.kind,
                "label": handle.label,
                "path": handle.path,
                "start_char": start_idx,
                "end_char": end_char,
                "total_chars": total_chars,
                "total_lines": total_lines,
                "text": slice_text,
                "preview": _collapse_preview(slice_text),
            }
        lines = text.splitlines()
        start_idx = max(0, int(start_line) - 1)
        end_idx = min(len(lines), start_idx + max(0, int(num_lines)))
        slice_lines = lines[start_idx:end_idx]
        slice_text = "\n".join(slice_lines)
        end_line = start_idx + len(slice_lines)
        return {
            "status": "ok",
            "handle_id": handle_id,
            "kind": handle.kind,
            "label": handle.label,
            "path": handle.path,
            "start_line": start_idx + 1 if slice_lines else int(start_line),
            "end_line": end_line if slice_lines else int(start_line),
            "total_chars": total_chars,
            "total_lines": total_lines,
            "text": slice_text,
            "preview": _collapse_preview(slice_text),
        }

    def read_file_slice(
        self, path: str, start_line: int = 1, num_lines: int = 100
    ) -> dict[str, object]:
        target = self._resolve_path(path)
        if not target.exists():
            return {
                "status": "error",
                "error": f"File not found: {_display_path(target, self.repo_path)}",
            }
        if target.is_dir():
            return {
                "status": "error",
                "error": f"Cannot read lines from directory: {_display_path(target, self.repo_path)}",
            }
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + max(0, num_lines))
        numbered = [
            {"line": start_idx + index + 1, "text": text}
            for index, text in enumerate(lines[start_idx:end_idx])
        ]
        return {
            "status": "ok",
            "path": _display_path(target, self.repo_path),
            "start_line": start_line,
            "lines": numbered,
            "returned_count": len(numbered),
            "total_lines": total_lines,
        }

    def grep_repo(
        self, pattern: str, path: str = ".", include: str = ""
    ) -> dict[str, object]:
        target = self._resolve_path(path)
        if not target.exists():
            return {
                "status": "error",
                "error": f"Path not found: {_display_path(target, self.repo_path)}",
            }
        rg_result = _grep_repo_with_rg(
            pattern=pattern,
            target=target,
            include=include,
            repo_path=self.repo_path,
        )
        if rg_result is not None:
            return rg_result
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return {
                "status": "error",
                "pattern": pattern,
                "search_path": _display_path(target, self.repo_path),
                "include": include or "all files",
                "error": f"Invalid regex: {exc}",
            }
        hits: list[dict[str, object]] = []
        for candidate in _iter_search_files(target):
            relative_path = _display_path(candidate, self.repo_path)
            if include and not fnmatch.fnmatch(relative_path, include):
                continue
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines, start=1):
                if compiled.search(line):
                    hits.append({"path": relative_path, "line": index, "text": line})
                    if len(hits) >= 50:
                        return {
                            "status": "ok",
                            "pattern": pattern,
                            "search_path": _display_path(target, self.repo_path),
                            "include": include or "all files",
                            "count": len(hits),
                            "hits": hits[:20],
                        }
        return {
            "status": "ok",
            "pattern": pattern,
            "search_path": _display_path(target, self.repo_path),
            "include": include or "all files",
            "count": len(hits),
            "hits": hits[:20],
        }

    def chunk_text(
        self,
        text: str,
        strategy: str = "size",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> list[object]:
        strategy_norm = (strategy or "size").strip().lower()
        if strategy_norm == "size":
            return _chunk_by_size(text, size=size, overlap=overlap)
        if strategy_norm == "headers":
            return _chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
        if strategy_norm == "timestamps":
            return _chunk_by_timestamps(
                text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]"
            )
        if strategy_norm in {"json_keys", "json-keys"}:
            return _chunk_by_json_keys(text)
        raise ValueError(f"Unknown chunking strategy: {strategy}")

    def chunk_file(
        self,
        path: str,
        strategy: str = "size",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, object]:
        target = self._resolve_path(path)
        if not target.exists():
            return {
                "status": "error",
                "error": f"File not found: {_display_path(target, self.repo_path)}",
            }
        if target.is_dir():
            return {
                "status": "error",
                "error": f"Cannot chunk directory: {_display_path(target, self.repo_path)}",
            }
        try:
            text = self.read_file(path)
            chunks = self.chunk_text(
                text,
                strategy=strategy,
                size=size,
                overlap=overlap,
                pattern=pattern,
            )
        except Exception as exc:
            return {
                "status": "error",
                "path": _display_path(target, self.repo_path),
                "strategy": strategy,
                "error": str(exc),
            }
        return {
            "status": "ok",
            "path": _display_path(target, self.repo_path),
            "strategy": strategy,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "preview": chunks[0] if chunks else "",
        }

    def _normalize_submit_schema(self, raw: object) -> list[dict[str, str | None]]:
        if not isinstance(raw, list):
            return []
        fields: list[dict[str, str | None]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if (
                not name
                or not name.isidentifier()
                or keyword.iskeyword(name)
                or name in seen
            ):
                continue
            seen.add(name)
            type_expr = str(item.get("type", "") or "").strip() or None
            fields.append({"name": name, "type": type_expr})
        return fields

    def _normalize_submit_value(
        self, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> object:
        if kwargs:
            return {
                str(key): value for key, value in kwargs.items() if value is not None
            }
        if not args:
            return {}
        if self.submit_schema:
            mapped: dict[str, object] = {}
            for index, field in enumerate(self.submit_schema):
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

    def _submit_impl(
        self,
        *args: object,
        _finalization_mode: str = "SUBMIT",
        _variable_name: str | None = None,
        **kwargs: object,
    ) -> object:
        value = self._normalize_submit_value(args, kwargs)
        self.final_artifact = {
            "kind": "markdown",
            "value": value,
            "variable_name": _variable_name,
            "finalization_mode": _finalization_mode,
        }
        return value

    def SUBMIT(self, *args: object, **kwargs: object) -> object:
        return self._submit_impl(*args, _finalization_mode="SUBMIT", **kwargs)

    def FINAL(self, value: object) -> object:
        if isinstance(value, dict):
            return self._submit_impl(_finalization_mode="FINAL", **value)
        if isinstance(value, str):
            return self._submit_impl(
                _finalization_mode="FINAL",
                final_markdown=value,
            )
        return self._submit_impl(value, _finalization_mode="FINAL")

    def FINAL_VAR(self, variable_name: str) -> object:
        if variable_name not in self.state:
            raise NameError(f"Variable '{variable_name}' is not defined")
        value = self.state[variable_name]
        if isinstance(value, dict):
            return self._submit_impl(
                _finalization_mode="FINAL_VAR",
                _variable_name=variable_name,
                **value,
            )
        if isinstance(value, str):
            return self._submit_impl(
                _finalization_mode="FINAL_VAR",
                _variable_name=variable_name,
                final_markdown=value,
            )
        return self._submit_impl(
            value,
            _finalization_mode="FINAL_VAR",
            _variable_name=variable_name,
        )

    def _install_helpers(self) -> None:
        self.state.update(
            {
                "run": self.run,
                "read_file": self.read_file,
                "store_prompt": self.store_prompt,
                "list_prompts": self.list_prompts,
                "read_prompt_slice": self.read_prompt_slice,
                "read_file_slice": self.read_file_slice,
                "list_files": self.list_files,
                "find_files": self.find_files,
                "grep_repo": self.grep_repo,
                "chunk_text": self.chunk_text,
                "chunk_file": self.chunk_file,
                "llm_query": self._wrapped_llm_query,
                "llm_query_batched": self._wrapped_llm_query_batched,
                "rlm_query": self._wrapped_llm_query,
                "rlm_query_batched": self._wrapped_llm_query_batched,
                "SUBMIT": self.SUBMIT,
                "FINAL": self.FINAL,
                "FINAL_VAR": self.FINAL_VAR,
            }
        )

    def _wrapped_llm_query(self, task: object) -> str:
        self.callback_count += 1
        return self.recurse_one(task)

    def _wrapped_llm_query_batched(self, tasks: list[object]) -> list[str]:
        self.callback_count += 1
        return self.recurse_many(tasks)

    def execute(
        self,
        *,
        code: str,
        submit_schema: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str, str | None, dict[str, Any] | None, int, int]:
        self.submit_schema = self._normalize_submit_schema(submit_schema)
        self.final_artifact = None
        self.callback_count = 0
        stdout = io.StringIO()
        stderr = io.StringIO()
        error_text: str | None = None
        started = time.perf_counter()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(code, self.state, self.state)
            except Exception:
                error_text = traceback.format_exc(limit=8)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return (
            stdout.getvalue(),
            stderr.getvalue(),
            error_text,
            self.final_artifact,
            duration_ms,
            self.callback_count,
        )


@dataclass(slots=True)
class _ChildRunPayload:
    run_id: str
    node_id: str
    parent_id: str
    depth: int
    repo: str
    ref: str | None
    task: str
    budget: RolloutBudget
    lm_config: SandboxLmRuntimeConfig
    remaining_sandboxes: int
    deadline_epoch_s: float
    sandbox_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "repo": self.repo,
            "ref": self.ref,
            "task": self.task,
            "budget": asdict(self.budget),
            "lm_config": self.lm_config.to_dict(),
            "remaining_sandboxes": self.remaining_sandboxes,
            "deadline_epoch_s": self.deadline_epoch_s,
            "sandbox_id": self.sandbox_id,
        }


@dataclass(slots=True)
class _ActiveChildRuntime:
    node_id: str
    parent_id: str
    sandbox: Any
    sandbox_id: str | None
    repo_path: str
    session_id: str
    command_id: str
    stdout_offset: int = 0
    frame_buffer: str = ""
    status: str = "bootstrapping"
    started_at_ms: int = 0


class SelfOrchestratedNodeRuntime:
    """Run one Daytona node entirely inside the sandbox."""

    def __init__(
        self,
        *,
        request_id: str,
        run_id: str,
        node_id: str,
        parent_id: str | None,
        depth: int,
        repo: str,
        ref: str | None,
        task: str,
        repo_path: str,
        sandbox_id: str | None,
        budget: RolloutBudget,
        lm_config: SandboxLmRuntimeConfig,
        remaining_sandboxes: int,
        deadline_epoch_s: float,
        emit_event: Callable[[str, str, dict[str, Any] | None], None],
        cancel_check: Callable[[], bool],
    ) -> None:
        self.request_id = request_id
        self.run_id = run_id
        self.node_id = node_id
        self.parent_id = parent_id
        self.depth = depth
        self.repo = repo
        self.ref = ref
        self.task = task
        self.repo_path = repo_path
        self.sandbox_id = sandbox_id
        self.budget = budget
        self.lm_config = lm_config
        self.remaining_sandboxes = max(1, remaining_sandboxes)
        self.deadline_epoch_s = deadline_epoch_s
        self._emit = emit_event
        self._cancel_check = cancel_check
        self._started_at = time.monotonic()
        self._nodes: dict[str, AgentNode] = {}
        self._sandboxes_used = 1
        self._client: Daytona | None = None
        self._state_lock = threading.Lock()
        self._active_child_runtimes: dict[str, _ActiveChildRuntime] = {}
        self._cancellation_started = False
        self._cancellation_warnings: list[str] = []
        self._lm = dspy.LM(
            lm_config.model,
            api_key=lm_config.api_key,
            api_base=lm_config.api_base,
            max_tokens=lm_config.max_tokens,
        )

    def run(self) -> DaytonaRunResult:
        final_artifact: FinalArtifact | None = None
        termination_reason = "completed"
        error_text: str | None = None
        try:
            final_artifact = self._run_current_node()
        except DaytonaRunCancelled as exc:
            termination_reason = "cancelled"
            error_text = str(exc)
        except TimeoutError as exc:
            termination_reason = "timeout"
            error_text = str(exc)
            raise
        except Exception as exc:
            termination_reason = "error"
            error_text = str(exc)
            raise
        finally:
            summary = RolloutSummary(
                duration_ms=int((time.monotonic() - self._started_at) * 1000),
                sandboxes_used=self._sandboxes_used,
                termination_reason=termination_reason,
                error=error_text,
                warnings=list(self._cancellation_warnings),
            )
        return DaytonaRunResult(
            run_id=self.run_id,
            repo=self.repo,
            ref=self.ref,
            task=self.task,
            budget=self.budget,
            root_id=self.node_id,
            nodes=self._nodes,
            final_artifact=final_artifact,
            summary=summary,
        )

    def _run_current_node(self) -> FinalArtifact | None:
        self._assert_not_cancelled()
        self._assert_time_budget()
        node = AgentNode(
            node_id=self.node_id,
            parent_id=self.parent_id,
            depth=self.depth,
            task=self.task,
            repo=self.repo,
            ref=self.ref,
            sandbox_id=self.sandbox_id,
            workspace_path=self.repo_path,
        )
        self._nodes[node.node_id] = node
        self._emit_status(
            node=node,
            text="Starting self-orchestrated Daytona node.",
            phase="node_start",
        )
        kernel = SandboxExecutionKernel(
            repo_path=self.repo_path,
            recurse_one=lambda raw_task: self._recursive_query(
                node=node, raw_task=raw_task
            ),
            recurse_many=lambda raw_tasks: self._recursive_query_batched(
                node=node, raw_tasks=raw_tasks
            ),
        )
        system_prompt = build_system_prompt(
            repo_path=self.repo_path, budget=self.budget
        )
        user_prompt = build_user_prompt(repo=self.repo, ref=self.ref)
        observation_text = "No execution has happened yet."

        for iteration in range(1, self.budget.max_iterations + 1):
            self._assert_not_cancelled(node=node)
            self._assert_time_budget()
            self._emit_status(
                node=node,
                text=f"Running Daytona iteration {iteration} at depth {self.depth}.",
                phase="iteration",
            )
            prompt = self._build_iteration_prompt(
                kernel=kernel,
                node=node,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                task=self.task,
                observation_text=observation_text,
                iteration=iteration,
            )
            response_text = self._call_lm(prompt)
            node.iteration_count = iteration
            node.prompt_previews.append(_preview_text(prompt))
            node.response_previews.append(_preview_text(response_text))
            code = self._extract_code(response_text)
            if code is None:
                fallback = FinalArtifact(
                    kind="markdown",
                    value=response_text,
                    finalization_mode="fallback",
                )
                if self._accept_final_artifact(node=node, artifact=fallback):
                    node.final_artifact = fallback
                    node.status = "completed"
                    self._emit_status(
                        node=node, text="Node completed.", phase="completed"
                    )
                    return fallback
                observation_text = self._build_root_retry_observation(
                    artifact=fallback,
                    base_observation=(
                        "The previous response did not produce executable code "
                        "or an acceptable synthesized final answer."
                    ),
                )
                continue

            stdout, stderr, error, final_artifact_raw, duration_ms, callback_count = (
                kernel.execute(code=code, submit_schema=_GUIDE_SUBMIT_SCHEMA)
            )
            observation = ExecutionObservation(
                iteration=iteration,
                code=code,
                stdout=_preview_text(stdout),
                stderr=_preview_text(stderr),
                error=_preview_text(error) if error else None,
                duration_ms=duration_ms,
                callback_count=callback_count,
            )
            node.observations.append(observation)
            self._emit_status(
                node=node,
                text=f"Iteration {iteration} executed in {duration_ms}ms.",
                phase="observation",
            )
            if final_artifact_raw is not None:
                artifact = FinalArtifact.from_raw(final_artifact_raw)
                if self._accept_final_artifact(node=node, artifact=artifact):
                    node.final_artifact = artifact
                    node.status = "completed"
                    self._emit_status(
                        node=node, text="Node completed.", phase="completed"
                    )
                    return artifact
                observation_text = self._build_root_retry_observation(
                    artifact=artifact,
                    base_observation=self._render_observation(observation),
                )
                continue
            if self._is_fatal_execution_error(error):
                node.status = "error"
                node.error = error
                artifact = FinalArtifact(
                    kind="error", value=error, finalization_mode="error"
                )
                node.final_artifact = artifact
                self._emit_status(node=node, text="Node failed.", phase="error")
                return artifact
            observation_text = self._render_observation(observation)

        node.status = "error"
        node.error = (
            f"Exceeded max_iterations={self.budget.max_iterations} without SUBMIT()"
        )
        artifact = FinalArtifact(
            kind="error", value=node.error, finalization_mode="error"
        )
        node.final_artifact = artifact
        self._emit_status(node=node, text="Node failed.", phase="error")
        return artifact

    def _build_iteration_prompt(
        self,
        *,
        kernel: SandboxExecutionKernel,
        node: AgentNode,
        system_prompt: str,
        user_prompt: str,
        task: str,
        observation_text: str,
        iteration: int,
    ) -> str:
        task_externalized = len(str(task or "")) > _INLINE_PROMPT_LIMIT
        observation_externalized = (
            len(str(observation_text or "")) > _INLINE_PROMPT_LIMIT
        )
        task_section = self._externalized_prompt_section(
            kernel=kernel,
            node=node,
            title="Task",
            text=task,
            kind="task",
            label=f"node-{node.node_id}-task",
        )
        observation_section = self._externalized_prompt_section(
            kernel=kernel,
            node=node,
            title="Previous observation",
            text=observation_text,
            kind="observation",
            label=f"node-{node.node_id}-iteration-{iteration - 1}-observation",
        )
        manifest_section = ""
        if task_externalized or observation_externalized or node.prompt_handles:
            manifest = self._sync_prompt_manifest(kernel=kernel, node=node)
            manifest_section = self._render_prompt_manifest(manifest)
        sections = [
            system_prompt,
            user_prompt,
            f"Iteration: {iteration}",
            task_section,
            observation_section,
            manifest_section,
        ]
        return "\n\n".join(section for section in sections if section.strip())

    def _externalized_prompt_section(
        self,
        *,
        kernel: SandboxExecutionKernel,
        node: AgentNode,
        title: str,
        text: str,
        kind: str,
        label: str,
    ) -> str:
        content = str(text or "")
        if len(content) <= _INLINE_PROMPT_LIMIT:
            if "\n" not in content:
                return f"{title}: {content}"
            return f"{title}:\n{content}"
        handle = next(
            (
                item
                for item in node.prompt_handles
                if item.kind == kind and item.label == label
            ),
            None,
        )
        if handle is None:
            handle = PromptHandle.from_raw(
                kernel.store_prompt(content, kind=kind, label=label)
            )
            self._record_prompt_handle(node=node, handle=handle)
        return (
            f"{title}: externalized as prompt handle `{handle.handle_id}`.\n"
            f"- kind: {handle.kind}\n"
            f"- label: {handle.label or 'none'}\n"
            f"- path: {handle.path}\n"
            f"- chars: {handle.char_count}\n"
            f"- lines: {handle.line_count}\n"
            f"- preview: {handle.preview or '[empty]'}\n"
            "Inspect it from executed Python with list_prompts() or "
            "read_prompt_slice(handle_id=..., start_line=..., num_lines=...)."
        )

    def _sync_prompt_manifest(
        self, *, kernel: SandboxExecutionKernel, node: AgentNode
    ) -> PromptManifest:
        manifest = PromptManifest.from_raw(kernel.list_prompts())
        existing = {handle.handle_id for handle in node.prompt_handles}
        for handle in manifest.handles:
            if handle.handle_id in existing:
                continue
            node.prompt_handles.append(handle)
            existing.add(handle.handle_id)
        return manifest

    @staticmethod
    def _record_prompt_handle(*, node: AgentNode, handle: PromptHandle) -> None:
        if any(item.handle_id == handle.handle_id for item in node.prompt_handles):
            return
        node.prompt_handles.append(handle)

    @staticmethod
    def _render_prompt_manifest(manifest: PromptManifest) -> str:
        if not manifest.handles:
            return (
                "Prompt manifest: no externalized prompt objects yet. "
                "Use store_prompt(...) only when you need to preserve large "
                "derived context across iterations."
            )
        lines = ["Prompt manifest (externalized context available inside the sandbox):"]
        for handle in manifest.handles:
            lines.append(
                "- "
                f"{handle.handle_id}: kind={handle.kind}, "
                f"label={handle.label or 'none'}, "
                f"path={handle.path}, chars={handle.char_count}, "
                f"lines={handle.line_count}, "
                f"preview={handle.preview or '[empty]'}"
            )
        lines.append(
            "Use list_prompts() for the full manifest and read_prompt_slice(...) "
            "to inspect bounded slices instead of expecting long externalized "
            "content inline."
        )
        return "\n".join(lines)

    def _call_lm(self, prompt: str) -> str:
        with dspy.context(lm=self._lm):
            response = self._lm(prompt)
        return _coerce_lm_output(response)

    @staticmethod
    def _extract_code(response_text: str) -> str | None:
        match = _CODE_BLOCK_RE.search(response_text)
        if match is not None:
            return match.group(1).strip()
        return None

    def _recursive_query(self, *, node: AgentNode, raw_task: object) -> str:
        task_spec = RecursiveTaskSpec.from_raw(raw_task)
        self._emit_tool_call(
            node=node,
            callback_name="llm_query",
            tool_input={"task": task_spec.to_dict()},
        )
        child_result = self._spawn_child_task(node=node, task_spec=task_spec)
        node.child_ids.append(child_result.child_id or "")
        node.child_links.append(
            ChildLink(
                child_id=child_result.child_id,
                callback_name="llm_query",
                task=task_spec,
                result_preview=child_result.result_preview,
                status=child_result.status,
            )
        )
        self._emit_tool_result(
            node=node, callback_name="llm_query", value=child_result.text
        )
        return child_result.text

    def _recursive_query_batched(
        self, *, node: AgentNode, raw_tasks: list[object]
    ) -> list[str]:
        if not isinstance(raw_tasks, list):
            raise ValueError("llm_query_batched expects a list of task specs.")
        task_specs = [RecursiveTaskSpec.from_raw(item) for item in raw_tasks]
        unique_specs: list[RecursiveTaskSpec] = []
        key_to_unique_index: dict[tuple[Any, ...], int] = {}
        unique_index_by_original_index: list[int] = []
        is_deduped_reuse_by_original_index: list[bool] = []
        for task_spec in task_specs:
            dedupe_key = self._task_dedupe_key(task_spec)
            if dedupe_key in key_to_unique_index:
                unique_index_by_original_index.append(key_to_unique_index[dedupe_key])
                is_deduped_reuse_by_original_index.append(True)
                continue
            unique_index = len(unique_specs)
            key_to_unique_index[dedupe_key] = unique_index
            unique_specs.append(task_spec)
            unique_index_by_original_index.append(unique_index)
            is_deduped_reuse_by_original_index.append(False)

        self._emit_tool_call(
            node=node,
            callback_name="llm_query_batched",
            tool_input={"tasks": [task_spec.to_dict() for task_spec in task_specs]},
        )
        results = self._spawn_child_tasks_batched(node=node, task_specs=unique_specs)

        values: list[str] = []
        for original_index, task_spec in enumerate(task_specs):
            unique_result = results[unique_index_by_original_index[original_index]]
            values.append(unique_result.text)
            node.child_ids.append(unique_result.child_id or "")
            node.child_links.append(
                ChildLink(
                    child_id=unique_result.child_id,
                    callback_name="llm_query_batched",
                    task=task_spec,
                    result_preview=unique_result.result_preview,
                    status=(
                        "deduped_reused"
                        if is_deduped_reuse_by_original_index[original_index]
                        else unique_result.status
                    ),
                )
            )
        self._emit_tool_result(
            node=node, callback_name="llm_query_batched", value=values
        )
        return values

    @staticmethod
    def _task_dedupe_key(task_spec: RecursiveTaskSpec) -> tuple[Any, ...]:
        source = task_spec.source
        source_key: Any = source.source_id or (
            source.kind,
            source.path,
            source.start_line,
            source.end_line,
            source.chunk_index,
            source.header,
            source.pattern,
        )
        return (task_spec.task, source_key)

    def _spawn_child_tasks_batched(
        self, *, node: AgentNode, task_specs: list[RecursiveTaskSpec]
    ) -> list[ChildTaskResult]:
        if not task_specs:
            return []
        self._assert_not_cancelled(node=node)
        available_children_budget = self.remaining_sandboxes - 1
        if available_children_budget < len(task_specs):
            raise RuntimeError(
                f"Sandbox budget exceeded: need {len(task_specs)} child sandboxes, "
                f"have {available_children_budget} remaining."
            )
        max_workers = max(1, min(len(task_specs), self.budget.batch_concurrency))
        shares: list[int] = []
        remaining = available_children_budget
        remaining_tasks = len(task_specs)
        for _ in task_specs:
            share = max(1, remaining // remaining_tasks)
            shares.append(share)
            remaining -= share
            remaining_tasks -= 1

        results: dict[int, ChildTaskResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._spawn_child_task,
                    node=node,
                    task_spec=task_spec,
                    child_remaining_sandboxes=shares[index],
                ): index
                for index, task_spec in enumerate(task_specs)
            }
            for future in as_completed(future_map):
                results[future_map[future]] = future.result()
        return [results[index] for index in range(len(task_specs))]

    def _spawn_child_task(
        self,
        *,
        node: AgentNode,
        task_spec: RecursiveTaskSpec,
        child_remaining_sandboxes: int | None = None,
    ) -> ChildTaskResult:
        self._assert_not_cancelled(node=node)
        self._assert_time_budget()
        if node.depth + 1 > self.budget.max_depth:
            raise RuntimeError(
                f"Recursive depth exceeded: {node.depth + 1} > max_depth={self.budget.max_depth}"
            )
        if self.remaining_sandboxes <= 1 and child_remaining_sandboxes is None:
            raise RuntimeError(
                f"Sandbox budget exceeded: {self.remaining_sandboxes} remaining."
            )
        child_client = self._client_or_create()
        sandbox = child_client.create()
        child_node_id = uuid.uuid4().hex
        child_node = AgentNode(
            node_id=child_node_id,
            parent_id=node.node_id,
            depth=node.depth + 1,
            task=task_spec.task,
            repo=self.repo,
            ref=self.ref,
            sandbox_id=getattr(sandbox, "id", None),
            status="bootstrapping",
        )
        with self._state_lock:
            self._nodes[child_node_id] = child_node
        try:
            child_sandbox_id = getattr(sandbox, "id", None)
            repo_path = self._build_repo_path(sandbox, self.repo)
            child_node.sandbox_id = child_sandbox_id
            child_node.workspace_path = repo_path
            self._clone_repo(
                sandbox=sandbox,
                repo_url=self.repo,
                ref=self.ref,
                repo_path=repo_path,
            )
            child_payload = _ChildRunPayload(
                run_id=self.run_id,
                node_id=child_node_id,
                parent_id=node.node_id,
                depth=node.depth + 1,
                repo=self.repo,
                ref=self.ref,
                task=task_spec.task,
                budget=self.budget,
                lm_config=self.lm_config,
                remaining_sandboxes=child_remaining_sandboxes
                or max(1, self.remaining_sandboxes - 1),
                deadline_epoch_s=self.deadline_epoch_s,
                sandbox_id=child_sandbox_id,
            )
            child_request_path = (
                PurePosixPath(repo_path) / ".fleet-rlm" / "run-request.json"
            )
            sandbox.fs.create_folder(str(child_request_path.parent), "755")
            sandbox.fs.upload_file(
                json.dumps(child_payload.to_dict(), ensure_ascii=False).encode("utf-8"),
                str(child_request_path),
            )
            command = (
                f"{self._env_exports()} && cd {self._quote(repo_path)} && "
                "uv run --python 3.12 python -m fleet_rlm.daytona_rlm.sandbox_controller "
                f"--child-request {self._quote(str(child_request_path))}"
            )
            self._emit_status(
                node=child_node,
                text=(
                    f"Spawning child sandbox for {task_spec.label or task_spec.task[:60]}."
                ),
                phase="child_spawn",
            )
            sandbox.process.create_session(f"fleet-rlm-child-{child_node_id}")
            request = SessionExecuteRequest(
                command=command,
                run_async=True,
                suppress_input_echo=True,
            )
            response = sandbox.process.execute_session_command(
                f"fleet-rlm-child-{child_node_id}",
                request,
                timeout=max(1, min(120, int(self._remaining_timeout()))),
            )
            child_runtime = _ActiveChildRuntime(
                node_id=child_node_id,
                parent_id=node.node_id,
                sandbox=sandbox,
                sandbox_id=child_sandbox_id,
                repo_path=repo_path,
                session_id=f"fleet-rlm-child-{child_node_id}",
                command_id=str(response.cmd_id),
                status="running",
                started_at_ms=int(time.time() * 1000),
            )
            self._register_child_runtime(child_runtime)
            child_node.status = "running"
            self._emit_status(
                node=child_node,
                text="Child sandbox started.",
                phase="child_bootstrap",
            )
            child_result = self._wait_for_child_result(
                parent_node=node,
                child_node=child_node,
                child_runtime=child_runtime,
            )
        finally:
            self._unregister_child_runtime(child_node_id)
            with contextlib.suppress(Exception):
                sandbox.process.delete_session(f"fleet-rlm-child-{child_node_id}")
            with contextlib.suppress(Exception):
                sandbox.delete()
        with self._state_lock:
            self._sandboxes_used += child_result.summary.sandboxes_used
            self.remaining_sandboxes = max(
                1, self.remaining_sandboxes - child_result.summary.sandboxes_used
            )
            self._merge_child_run_result(child_result)
        artifact = child_result.final_artifact
        rendered = self._render_child_result(artifact)
        return ChildTaskResult(
            child_id=child_result.root_id,
            task=task_spec,
            text=rendered,
            result_preview=self._build_result_preview(artifact, fallback_text=rendered),
            status=self._child_status_from_result(child_result),
        )

    def _wait_for_child_result(
        self,
        *,
        parent_node: AgentNode,
        child_node: AgentNode,
        child_runtime: _ActiveChildRuntime,
    ) -> DaytonaRunResult:
        while True:
            self._assert_time_budget()
            self._assert_not_cancelled(node=parent_node)
            for frame in self._drain_child_runtime_frames(child_runtime):
                frame_type = str(frame.get("type", "") or "")
                if frame_type == RunEventFrame(request_id="", kind="").type:
                    event = RunEventFrame.from_dict(frame)
                    self._handle_child_runtime_event(
                        parent_node=parent_node,
                        child_node=child_node,
                        child_runtime=child_runtime,
                        frame=event,
                    )
                    continue
                if frame_type == RunResultEnvelope(request_id="", result={}).type:
                    return DaytonaRunResult.from_raw(frame.get("result"))
                if frame_type == RunErrorEnvelope(request_id="", error="").type:
                    error = RunErrorEnvelope.from_dict(frame)
                    if error.category == "cancelled":
                        child_node.status = "cancelled"
                        child_node.error = error.error
                        raise DaytonaRunCancelled(error.error)
                    child_node.status = "error"
                    child_node.error = error.error
                    raise RuntimeError(error.error)
            time.sleep(0.05)

    def _drain_child_runtime_frames(
        self, child_runtime: _ActiveChildRuntime
    ) -> list[dict[str, Any]]:
        logs = child_runtime.sandbox.process.get_session_command_logs(
            child_runtime.session_id,
            child_runtime.command_id,
        )
        stdout = str(getattr(logs, "stdout", "") or "")
        new_text = stdout[child_runtime.stdout_offset :]
        child_runtime.stdout_offset = len(stdout)
        child_runtime.frame_buffer += new_text

        frames: list[dict[str, Any]] = []
        while "\n" in child_runtime.frame_buffer:
            line, child_runtime.frame_buffer = child_runtime.frame_buffer.split("\n", 1)
            decoded = decode_frame(line.strip())
            if decoded is not None:
                frames.append(decoded)
        return frames

    def _handle_child_runtime_event(
        self,
        *,
        parent_node: AgentNode,
        child_node: AgentNode,
        child_runtime: _ActiveChildRuntime,
        frame: RunEventFrame,
    ) -> None:
        payload = dict(frame.payload or {})
        live_node = self._upsert_node_from_event_payload(
            payload,
            fallback_node=child_node,
            fallback_parent_id=parent_node.node_id,
            fallback_depth=parent_node.depth + 1,
        )
        if live_node is not None:
            child_runtime.status = live_node.status
        warning_text = str(payload.get("warning") or "").strip()
        if warning_text:
            self._record_warning(warning=warning_text, node=live_node or child_node)
        self._emit(frame.kind, frame.text, payload)

    def _upsert_node_from_event_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback_node: AgentNode,
        fallback_parent_id: str,
        fallback_depth: int,
    ) -> AgentNode | None:
        node_payload = payload.get("node")
        if not isinstance(node_payload, dict):
            node_payload = payload if payload.get("node_id") else None
        if not isinstance(node_payload, dict):
            return None
        node_id = str(node_payload.get("node_id") or payload.get("node_id") or "")
        if not node_id:
            return None
        existing = self._nodes.get(node_id)
        if existing is None:
            existing = AgentNode(
                node_id=node_id,
                parent_id=str(
                    node_payload.get("parent_id")
                    or payload.get("parent_id")
                    or fallback_parent_id
                )
                or None,
                depth=int(
                    node_payload.get("depth") or payload.get("depth") or fallback_depth
                ),
                task=str(node_payload.get("task") or f"Node {node_id[:8]}"),
                repo=self.repo,
                ref=self.ref,
                sandbox_id=str(
                    node_payload.get("sandbox_id")
                    or payload.get("sandbox_id")
                    or fallback_node.sandbox_id
                    or ""
                )
                or None,
                workspace_path=str(node_payload.get("workspace_path") or "") or None,
            )
        existing.parent_id = (
            str(
                node_payload.get("parent_id")
                or payload.get("parent_id")
                or existing.parent_id
                or ""
            )
            or existing.parent_id
        )
        existing.depth = int(
            node_payload.get("depth") or payload.get("depth") or existing.depth
        )
        existing.task = str(node_payload.get("task") or existing.task)
        existing.status = str(
            node_payload.get("status")
            or payload.get("status")
            or payload.get("node_status")
            or existing.status
        )
        existing.sandbox_id = (
            str(
                node_payload.get("sandbox_id")
                or payload.get("sandbox_id")
                or existing.sandbox_id
                or ""
            )
            or existing.sandbox_id
        )
        workspace_path = str(
            node_payload.get("workspace_path") or existing.workspace_path or ""
        )
        existing.workspace_path = workspace_path or existing.workspace_path
        existing.iteration_count = int(
            node_payload.get("iteration_count")
            or payload.get("iteration_count")
            or existing.iteration_count
        )
        error_text = str(node_payload.get("error") or payload.get("error") or "")
        if error_text:
            existing.error = error_text
        prompt_items = (
            node_payload.get("prompt_handles") or payload.get("prompt_handles") or []
        )
        if isinstance(prompt_items, list):
            existing.prompt_handles = [
                PromptHandle.from_raw(item)
                for item in prompt_items
                if isinstance(item, dict)
            ] or existing.prompt_handles
        child_links_raw = (
            node_payload.get("child_links") or payload.get("child_links") or []
        )
        if isinstance(child_links_raw, list):
            parsed_links = [
                ChildLink.from_raw(item)
                for item in child_links_raw
                if isinstance(item, dict)
            ]
            if parsed_links:
                existing.child_links = parsed_links
                existing.child_ids = [
                    item.child_id or ""
                    for item in parsed_links
                    if item.child_id is not None
                ]
        warnings_raw = node_payload.get("warnings") or payload.get("warnings") or []
        if isinstance(warnings_raw, list):
            existing.warnings = [
                str(item) for item in warnings_raw if item is not None
            ] or existing.warnings
        final_raw = node_payload.get("final_artifact") or payload.get("final_artifact")
        if isinstance(final_raw, dict):
            existing.final_artifact = FinalArtifact.from_raw(final_raw)
        with self._state_lock:
            self._nodes[node_id] = existing
        return existing

    def _merge_child_run_result(self, child_result: DaytonaRunResult) -> None:
        for child_node_id_key, child_node in child_result.nodes.items():
            existing = self._nodes.get(child_node_id_key)
            if existing is None:
                self._nodes[child_node_id_key] = child_node
                continue
            existing.parent_id = child_node.parent_id
            existing.depth = child_node.depth
            existing.task = child_node.task
            existing.repo = child_node.repo
            existing.ref = child_node.ref
            existing.sandbox_id = child_node.sandbox_id or existing.sandbox_id
            existing.workspace_path = (
                child_node.workspace_path or existing.workspace_path
            )
            existing.status = child_node.status or existing.status
            existing.prompt_handles = (
                child_node.prompt_handles or existing.prompt_handles
            )
            existing.prompt_previews = (
                child_node.prompt_previews or existing.prompt_previews
            )
            existing.response_previews = (
                child_node.response_previews or existing.response_previews
            )
            existing.observations = child_node.observations or existing.observations
            existing.child_ids = child_node.child_ids or existing.child_ids
            existing.child_links = child_node.child_links or existing.child_links
            existing.warnings = child_node.warnings or existing.warnings
            existing.final_artifact = (
                child_node.final_artifact or existing.final_artifact
            )
            existing.iteration_count = (
                child_node.iteration_count or existing.iteration_count
            )
            existing.error = child_node.error or existing.error

    def _child_status_from_result(self, result: DaytonaRunResult) -> str:
        if result.summary.termination_reason == "cancelled":
            if result.summary.warnings:
                return "cancel_failed"
            return "cancelled"
        return self._child_status_from_artifact(result.final_artifact)

    def _client_or_create(self) -> Daytona:
        if self._client is None:
            resolved = resolve_daytona_config(env=os.environ)
            self._client = Daytona(
                DaytonaConfig(
                    api_key=resolved.api_key,
                    api_url=resolved.api_url,
                    target=resolved.target,
                )
            )
        return self._client

    @staticmethod
    def _build_repo_path(sandbox: Any, repo_url: str) -> str:
        work_dir = (
            sandbox.get_work_dir() if hasattr(sandbox, "get_work_dir") else "/workspace"
        )
        repo_name = _safe_repo_name(repo_url)
        return str(PurePosixPath(work_dir) / "workspace" / repo_name)

    @staticmethod
    def _clone_repo(
        *, sandbox: Any, repo_url: str, ref: str | None, repo_path: str
    ) -> None:
        work_dir = (
            sandbox.get_work_dir() if hasattr(sandbox, "get_work_dir") else "/workspace"
        )
        sandbox.fs.create_folder(str(PurePosixPath(work_dir) / "workspace"), "755")
        clone_kwargs: dict[str, Any] = {"url": repo_url, "path": repo_path}
        if ref:
            if _looks_like_commit(ref):
                clone_kwargs["commit_id"] = ref
            else:
                clone_kwargs["branch"] = ref
        sandbox.git.clone(**clone_kwargs)

    def _env_exports(self) -> str:
        env_pairs = {
            "DAYTONA_API_KEY": os.environ.get("DAYTONA_API_KEY", ""),
            "DAYTONA_API_URL": os.environ.get("DAYTONA_API_URL", ""),
            "DAYTONA_TARGET": os.environ.get("DAYTONA_TARGET", ""),
            "DSPY_LM_MODEL": self.lm_config.model,
            "DSPY_LLM_API_KEY": self.lm_config.api_key,
            "DSPY_LM_API_BASE": self.lm_config.api_base or "",
            "DSPY_LM_MAX_TOKENS": str(self.lm_config.max_tokens),
            "DSPY_DELEGATE_LM_MODEL": self.lm_config.delegate_model or "",
            "DSPY_DELEGATE_LM_API_KEY": self.lm_config.delegate_api_key or "",
            "DSPY_DELEGATE_LM_API_BASE": self.lm_config.delegate_api_base or "",
        }
        parts = [
            f"export {key}={self._quote(value)}"
            for key, value in env_pairs.items()
            if value
        ]
        return "; ".join(parts)

    @staticmethod
    def _quote(value: str) -> str:
        return shlex.quote(value)

    @staticmethod
    def _parse_child_stdout(stdout: str) -> DaytonaRunResult:
        frames = [
            decoded
            for decoded in (decode_frame(line.strip()) for line in stdout.splitlines())
            if decoded is not None
        ]
        for frame in reversed(frames):
            if frame.get("type") == "run_result":
                return DaytonaRunResult.from_raw(frame.get("result"))
            if frame.get("type") == "run_error":
                raise RuntimeError(
                    str(frame.get("error") or "Child sandbox run failed")
                )
        raise RuntimeError("Child sandbox did not return a run result.")

    def _accept_final_artifact(
        self, *, node: AgentNode, artifact: FinalArtifact
    ) -> bool:
        if node.depth > 0:
            return True
        return self._root_finalization_candidate(artifact) is not None

    def _root_finalization_candidate(self, artifact: FinalArtifact) -> str | None:
        candidate = self._extract_synthesized_text(artifact.value)
        if candidate is None:
            return None
        normalized = _collapse_plain_text(candidate)
        if not normalized:
            return None
        if len(normalized) < _ROOT_MIN_CHARS:
            return None
        if len(_WORD_RE.findall(normalized)) < _ROOT_MIN_WORDS:
            return None
        if self._looks_like_unsynthesized_root_payload(
            value=artifact.value, raw_text=candidate
        ):
            return None
        return candidate

    @staticmethod
    def _extract_synthesized_text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("summary", "final_markdown"):
                candidate = value.get(key)
                if candidate is None:
                    continue
                text = str(candidate)
                if text.strip():
                    return text
        return None

    def _looks_like_unsynthesized_root_payload(
        self, *, value: Any, raw_text: str
    ) -> bool:
        if isinstance(value, (list, tuple)):
            return True
        if isinstance(value, dict) and self._extract_synthesized_text(value) is None:
            return True
        stripped = raw_text.strip()
        if (
            not stripped
            or stripped.startswith("```")
            or stripped.startswith("{")
            or stripped.startswith("[")
        ):
            return True
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) >= 2:
            if all(_PATH_LINE_RE.match(line) for line in lines):
                return True
            grep_like_count = sum(1 for line in lines if _GREP_LINE_RE.match(line))
            if grep_like_count >= max(2, len(lines) - 1):
                return True
        return False

    def _build_root_retry_observation(
        self, *, artifact: FinalArtifact, base_observation: str
    ) -> str:
        preview = self._build_result_preview(artifact)
        return (
            f"{base_observation}\n\n"
            "Your previous FINAL produced raw intermediate data instead of a "
            "human-readable synthesized answer. Reuse the repository evidence "
            "already stored in Python variables, summarize the key findings in "
            "concise markdown prose, and finalize with SUBMIT(...) using that "
            "summary text or a dict with 'summary' or 'final_markdown'. "
            "FINAL(...) and FINAL_VAR(...) remain available only as legacy aliases.\n\n"
            f"Rejected final preview: {preview or '[empty final output]'}"
        )

    def _render_observation(self, observation: ExecutionObservation) -> str:
        chunks = [f"Duration: {observation.duration_ms}ms"]
        if observation.callback_count:
            chunks.append(f"Recursive calls: {observation.callback_count}")
        if observation.stdout.strip():
            chunks.append(f"STDOUT:\n{observation.stdout.strip()}")
        if observation.stderr.strip():
            chunks.append(f"STDERR:\n{observation.stderr.strip()}")
        if observation.error:
            chunks.append(f"ERROR:\n{observation.error}")
        return "\n\n".join(chunks)

    @staticmethod
    def _is_fatal_execution_error(error: str | None) -> bool:
        if not error:
            return False
        fatal_markers = (
            "Recursive depth exceeded",
            "Sandbox budget exceeded",
            "Global timeout exceeded",
        )
        return any(marker in error for marker in fatal_markers)

    def _render_child_result(self, artifact: FinalArtifact | None) -> str:
        if artifact is None:
            return ""
        rendered = self._textual_render_for_result(artifact.value)
        return self._truncate_result_text(rendered)

    def _build_result_preview(
        self, artifact: FinalArtifact | None, *, fallback_text: str = ""
    ) -> str:
        if artifact is not None:
            candidate = self._extract_synthesized_text(artifact.value)
            if candidate is not None:
                return _collapse_preview(candidate, limit=280)
            rendered = self._textual_render_for_result(artifact.value)
            if rendered:
                return _collapse_preview(rendered, limit=280)
        return _collapse_preview(fallback_text, limit=280)

    @staticmethod
    def _child_status_from_artifact(artifact: FinalArtifact | None) -> str:
        if artifact is None:
            return "completed"
        if artifact.finalization_mode == "error":
            return "error"
        if artifact.finalization_mode == "cancelled":
            return "cancelled"
        return "completed"

    @staticmethod
    def _textual_render_for_result(value: Any) -> str:
        candidate = SelfOrchestratedNodeRuntime._extract_synthesized_text(value)
        if candidate is not None:
            return candidate
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    def _truncate_result_text(self, text: str) -> str:
        if len(text) <= self.budget.result_truncation_limit:
            return text
        return (
            text[: self.budget.result_truncation_limit].rstrip()
            + "\n\n[truncated child result]"
        )

    def _remaining_timeout(self) -> float:
        remaining = self.deadline_epoch_s - time.time()
        if remaining <= 0:
            raise TimeoutError("Global timeout exceeded.")
        return remaining

    def _assert_time_budget(self) -> None:
        _ = self._remaining_timeout()

    def _record_warning(
        self, *, warning: str, node: AgentNode | None = None
    ) -> str | None:
        warning_text = _collapse_preview(warning, limit=320)
        if not warning_text:
            return None
        with self._state_lock:
            if warning_text not in self._cancellation_warnings:
                self._cancellation_warnings.append(warning_text)
            if node is not None and warning_text not in node.warnings:
                node.warnings.append(warning_text)
        return warning_text

    def _emit_warning(
        self,
        *,
        node: AgentNode | None,
        text: str,
        warning: str,
        phase: str = "warning",
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload_extra = {"warning": warning}
        if extra:
            payload_extra.update(extra)
        self._emit(
            "warning",
            text,
            self._runtime_payload(node=node, phase=phase, extra=payload_extra),
        )

    def _register_child_runtime(self, child: _ActiveChildRuntime) -> None:
        with self._state_lock:
            self._active_child_runtimes[child.node_id] = child

    def _unregister_child_runtime(self, node_id: str) -> None:
        with self._state_lock:
            self._active_child_runtimes.pop(node_id, None)

    def _begin_tree_cancellation(self, *, node: AgentNode | None = None) -> None:
        active_children: list[_ActiveChildRuntime] = []
        with self._state_lock:
            if self._cancellation_started:
                return
            self._cancellation_started = True
            active_children = list(self._active_child_runtimes.values())
        if node is not None:
            node.status = "cancelling"
            node.error = "Cancellation requested."
            self._emit_status(
                node=node,
                text="Cancellation requested. Stopping descendant sandboxes.",
                phase="cancelling",
            )
        for child in active_children:
            self._cancel_child_runtime(child)

    def _cancel_child_runtime(self, child: _ActiveChildRuntime) -> None:
        child_node = self._nodes.get(child.node_id)
        if child_node is not None:
            child_node.status = "cancelling"
            child_node.error = "Cancellation requested."
            self._emit_status(
                node=child_node,
                text="Stopping descendant sandbox.",
                phase="cancelling",
            )
        try:
            if hasattr(child.sandbox, "stop"):
                child.sandbox.stop(
                    timeout=max(1, min(30, int(self._remaining_timeout())))
                )
            child.sandbox.delete()
        except Exception as exc:
            warning = self._record_warning(
                warning=(
                    "Failed to terminate descendant sandbox "
                    f"{child.sandbox_id or child.node_id}: {exc}"
                ),
                node=child_node,
            )
            if child_node is not None:
                child_node.status = "cancel_failed"
                child_node.error = warning or "Failed to terminate descendant sandbox."
            if warning is not None:
                self._emit_warning(
                    node=child_node,
                    text="Descendant sandbox did not terminate cleanly.",
                    warning=warning,
                    phase="cancel_failed",
                    extra={"status": "cancel_failed"},
                )
        else:
            if child_node is not None:
                child_node.status = "cancelled"
                child_node.error = "Request cancelled."
                self._emit_status(
                    node=child_node,
                    text="Descendant sandbox cancelled.",
                    phase="cancelled",
                )
        finally:
            self._unregister_child_runtime(child.node_id)

    def _assert_not_cancelled(self, *, node: AgentNode | None = None) -> None:
        if not self._cancel_check():
            return
        self._begin_tree_cancellation(node=node)
        if node is not None:
            node.status = "cancelled"
            node.error = "Request cancelled."
        raise DaytonaRunCancelled("Request cancelled.")

    def _runtime_payload(
        self,
        *,
        node: AgentNode | None = None,
        phase: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runtime": {
                "depth": node.depth if node is not None else self.depth,
                "max_depth": self.budget.max_depth,
                "execution_profile": "DAYTONA_PILOT_SELF_ORCHESTRATED",
                "sandbox_active": True,
                "effective_max_iters": self.budget.max_iterations,
                "execution_mode": "daytona_pilot",
                "runtime_mode": "daytona_pilot",
                "sandbox_id": node.sandbox_id if node is not None else self.sandbox_id,
                "run_id": self.run_id,
            },
            "run_id": self.run_id,
        }
        if node is not None:
            node_payload: dict[str, Any] = {
                "node_id": node.node_id,
                "parent_id": node.parent_id,
                "depth": node.depth,
                "task": node.task,
                "status": node.status,
                "sandbox_id": node.sandbox_id,
                "workspace_path": node.workspace_path,
                "prompt_handles": [handle.to_dict() for handle in node.prompt_handles],
                "prompt_manifest": {
                    "handles": [handle.to_dict() for handle in node.prompt_handles]
                },
                "child_links": [item.to_dict() for item in node.child_links],
                "warnings": list(node.warnings),
                "iteration_count": node.iteration_count,
            }
            if node.final_artifact is not None:
                node_payload["final_artifact"] = node.final_artifact.to_dict()
            if node.error:
                node_payload["error"] = node.error
            payload["node"] = node_payload
            payload["node_id"] = node.node_id
            payload["parent_id"] = node.parent_id
            payload["depth"] = node.depth
            payload["repo"] = node.repo
            payload["ref"] = node.ref
            payload["status"] = node.status
            payload["node_status"] = node.status
            payload["prompt_handles"] = [
                handle.to_dict() for handle in node.prompt_handles
            ]
            payload["child_links"] = [item.to_dict() for item in node.child_links]
            payload["warnings"] = list(node.warnings)
        if phase:
            payload["phase"] = phase
        if extra:
            payload.update(extra)
        return payload

    def _emit_status(self, *, node: AgentNode, text: str, phase: str) -> None:
        self._emit(
            "status",
            text,
            self._runtime_payload(node=node, phase=phase),
        )

    def _emit_tool_call(
        self, *, node: AgentNode, callback_name: str, tool_input: Any
    ) -> None:
        self._emit(
            "tool_call",
            f"Calling {callback_name}",
            self._runtime_payload(
                node=node,
                phase="recursive_call",
                extra={"tool_name": callback_name, "tool_input": tool_input},
            ),
        )

    def _emit_tool_result(
        self, *, node: AgentNode, callback_name: str, value: Any
    ) -> None:
        if isinstance(value, list):
            rendered: Any = {
                "count": len(value),
                "preview": value[: min(3, len(value))],
            }
        elif isinstance(value, dict):
            rendered = value
        else:
            rendered = str(value)
        self._emit(
            "tool_result",
            f"{callback_name} completed",
            self._runtime_payload(
                node=node,
                phase="recursive_result",
                extra={
                    "tool_name": callback_name,
                    "tool_output": rendered,
                    "status": "ok",
                },
            ),
        )


class _CancelWatcher:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self._cancelled = threading.Event()

    def start(self) -> None:
        thread = threading.Thread(target=self._reader_loop, daemon=True)
        thread.start()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _reader_loop(self) -> None:
        for raw in sys.stdin:
            frame = decode_frame(raw.strip())
            if frame is None:
                continue
            if (
                frame.get("type") == RunCancelRequest("", type="run_cancel").type
                and str(frame.get("request_id", "")) == self.request_id
            ):
                self._cancelled.set()
                return


def _emit_stream_event(
    request_id: str, kind: str, text: str, payload: dict[str, Any] | None = None
) -> None:
    _emit(
        RunEventFrame(
            request_id=request_id,
            kind=kind,
            text=text,
            payload=payload or {},
        ).to_dict()
    )


def _run_payload(
    payload: dict[str, Any],
    *,
    request_id: str,
    emit_events: bool = True,
    allow_cancel_from_stdin: bool = True,
) -> DaytonaRunResult:
    budget = RolloutBudget(**payload["budget"])
    runtime = SelfOrchestratedNodeRuntime(
        request_id=request_id,
        run_id=str(payload["run_id"]),
        node_id=str(payload["node_id"]),
        parent_id=str(payload.get("parent_id") or "") or None,
        depth=int(payload.get("depth", 0) or 0),
        repo=str(payload["repo"]),
        ref=(str(payload["ref"]) if payload.get("ref") else None),
        task=str(payload["task"]),
        repo_path=str(payload["repo_path"])
        if payload.get("repo_path")
        else str(pathlib.Path.cwd()),
        sandbox_id=(str(payload["sandbox_id"]) if payload.get("sandbox_id") else None),
        budget=budget,
        lm_config=SandboxLmRuntimeConfig.from_raw(payload["lm_config"]),
        remaining_sandboxes=int(
            payload.get("remaining_sandboxes", budget.max_sandboxes)
            or budget.max_sandboxes
        ),
        deadline_epoch_s=float(
            payload.get("deadline_epoch_s") or (time.time() + budget.global_timeout)
        ),
        emit_event=lambda kind, text, event_payload=None: (
            _emit_stream_event(
                request_id,
                kind,
                text,
                event_payload,
            )
            if emit_events
            else None
        ),
        cancel_check=lambda: (
            False
            if not allow_cancel_from_stdin
            else bool(_GLOBAL_CANCEL_WATCHER and _GLOBAL_CANCEL_WATCHER.cancelled())
        ),
    )
    return runtime.run()


def _run_child_request(request_path: str) -> int:
    payload = json.loads(pathlib.Path(request_path).read_text(encoding="utf-8"))
    request_id = f"child-{uuid.uuid4().hex}"
    payload["repo_path"] = payload.get("repo_path") or str(pathlib.Path.cwd())
    result = _run_payload(
        payload,
        request_id=request_id,
        emit_events=True,
        allow_cancel_from_stdin=False,
    )
    _emit(RunResultEnvelope(request_id=request_id, result=result.to_dict()).to_dict())
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--child-request", default=None)
    args = parser.parse_args(argv)

    if args.child_request:
        try:
            return _run_child_request(args.child_request)
        except Exception as exc:  # pragma: no cover - live path
            _emit(
                RunErrorEnvelope(
                    request_id=f"child-{uuid.uuid4().hex}",
                    error=str(exc),
                ).to_dict()
            )
            return 1

    _emit(RunReady().to_dict())
    for raw in sys.stdin:
        frame = decode_frame(raw.strip())
        if frame is None:
            continue
        if frame.get("type") != RunStartRequest("", {}).type:
            continue
        request = RunStartRequest(
            request_id=str(frame["request_id"]),
            payload=dict(frame.get("payload", {}) or {}),
        )
        request.payload["repo_path"] = request.payload.get("repo_path") or str(
            pathlib.Path.cwd()
        )
        global _GLOBAL_CANCEL_WATCHER
        _GLOBAL_CANCEL_WATCHER = _CancelWatcher(request.request_id)
        _GLOBAL_CANCEL_WATCHER.start()
        try:
            result = _run_payload(
                request.payload,
                request_id=request.request_id,
                emit_events=True,
                allow_cancel_from_stdin=True,
            )
            _emit(
                RunResultEnvelope(
                    request_id=request.request_id, result=result.to_dict()
                ).to_dict()
            )
        except Exception as exc:  # pragma: no cover - exercised via host/runtime tests
            _emit(
                RunErrorEnvelope(
                    request_id=request.request_id,
                    error=str(exc),
                ).to_dict()
            )
        finally:
            _GLOBAL_CANCEL_WATCHER = None
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess/runtime tests
    raise SystemExit(main())
