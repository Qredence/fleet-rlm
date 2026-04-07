"""Sandbox helper assets bundled into the driver process."""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from typing import Any

try:
    from fleet_rlm.utils.volume_tree import resolve_realpath_within_root
except ModuleNotFoundError:
    # The sandbox bootstraps from bundled source text, so package imports
    # are not available until the host explicitly installs this repo inside the
    # remote environment. Keep a local fallback so the bundled helpers remain
    # self-contained for live sandbox startup.
    def resolve_realpath_within_root(
        path: str,
        *,
        root: str,
        empty_error: str,
        invalid_error_prefix: str,
    ) -> tuple[str | None, str | None]:
        root_real = os.path.realpath(root)
        raw = str(path or "").strip()
        if not raw:
            return None, empty_error

        joined = (
            os.path.normpath(raw)
            if os.path.isabs(raw)
            else os.path.normpath(os.path.join(root, raw))
        )
        resolved = os.path.realpath(joined)
        if resolved != root_real and not resolved.startswith(root_real + os.sep):
            return None, f"{invalid_error_prefix}{raw}]"
        return resolved, None


def peek(text: str, start: int = 0, length: int = 2000) -> str:
    return text[start : start + length]


def grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
    lines = text.splitlines()
    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    hits: list[str] = []
    for idx, line in enumerate(lines):
        if pat.search(line):
            lo = max(0, idx - context)
            hi = min(len(lines), idx + context + 1)
            hits.append("\n".join(lines[lo:hi]))
    return hits


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
) -> list[dict[str, Any]]:
    if not text:
        return []

    compiled = re.compile(pattern, flags | re.MULTILINE)
    matches = list(compiled.finditer(text))
    if not matches:
        return [{"header": "", "content": text.strip(), "start_pos": 0}]

    parts: list[dict[str, Any]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            parts.append({"header": "", "content": preamble, "start_pos": 0})

    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        section = text[match.start() : end]
        newline_pos = section.find("\n")
        if newline_pos == -1:
            header = section.strip()
            content = ""
        else:
            header = section[:newline_pos].strip()
            content = section[newline_pos + 1 :].strip()
        parts.append({"header": header, "content": content, "start_pos": match.start()})
    return parts


def chunk_by_timestamps(
    text: str,
    pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]",
    flags: int = re.MULTILINE,
) -> list[dict[str, Any]]:
    if not text:
        return []

    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))
    if not matches:
        return [{"timestamp": "", "content": text, "start_pos": 0}]

    chunks: list[dict[str, Any]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"timestamp": "", "content": preamble, "start_pos": 0})

    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        chunks.append(
            {
                "timestamp": match.group(0).strip(),
                "content": content,
                "start_pos": match.start(),
            }
        )
    return chunks


def chunk_by_json_keys(text: str) -> list[dict[str, Any]]:
    if not text or not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
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


_buffers: dict[str, list[Any]] = {}


def add_buffer(name: str, value: Any) -> None:
    _buffers.setdefault(name, []).append(value)


def get_buffer(name: str) -> list[Any]:
    return list(_buffers.get(name, []))


def clear_buffer(name: str | None = None) -> None:
    if name is None:
        _buffers.clear()
    else:
        _buffers.pop(name, None)


def reset_buffers() -> None:
    _buffers.clear()


def _resolve_volume_path(path: str) -> tuple[str | None, str | None]:
    return resolve_realpath_within_root(
        path,
        root="/data",
        empty_error="[error: volume path cannot be empty]",
        invalid_error_prefix="[error: invalid volume path: ",
    )


def save_to_volume(path: str, content: str) -> str:
    base = "/data"
    if not os.path.isdir(base):
        return "[error: no volume mounted at /data]"
    full, path_error = _resolve_volume_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"
    os.makedirs(os.path.dirname(full) or base, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    try:
        subprocess.run(["sync", "/data"], check=False, capture_output=True)
    except Exception:
        pass
    return full


def load_from_volume(path: str) -> str:
    full, path_error = _resolve_volume_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"
    if not os.path.isfile(full):
        return f"[error: file not found: {full}]"
    with open(full, encoding="utf-8") as fh:
        return fh.read()


WORKSPACE_BASE = "/data/workspace"


def _resolve_workspace_path(path: str) -> tuple[str | None, str | None]:
    return resolve_realpath_within_root(
        path,
        root=WORKSPACE_BASE,
        empty_error="[error: workspace path cannot be empty]",
        invalid_error_prefix="[error: invalid workspace path: ",
    )


def workspace_write(path: str, content: str) -> str:
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error
    if not os.path.isdir("/data"):
        return "[error: no volume mounted at /data]"
    os.makedirs(WORKSPACE_BASE, exist_ok=True)
    if full is None:
        return "[error: invalid workspace path]"
    os.makedirs(os.path.dirname(full) or WORKSPACE_BASE, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    return full


def workspace_read(path: str) -> str:
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error
    if full is None:
        return "[error: invalid workspace path]"
    if not os.path.isfile(full):
        return f"[error: file not found: {full}]"
    with open(full, encoding="utf-8") as fh:
        return fh.read()


def workspace_list(pattern: str = "*") -> list[str]:
    if not os.path.isdir(WORKSPACE_BASE):
        return []
    search_path = os.path.join(WORKSPACE_BASE, "**", pattern)
    files = glob.glob(search_path, recursive=True)
    base_real = os.path.realpath(WORKSPACE_BASE)

    rel_paths: list[str] = []
    for found in files:
        if not os.path.isfile(found):
            continue
        found_real = os.path.realpath(found)
        if found_real != base_real and not found_real.startswith(base_real + os.sep):
            continue
        rel_paths.append(os.fsdecode(os.path.relpath(found_real, base_real)))
    return rel_paths


def workspace_append(path: str, content: str) -> str:
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error
    if not os.path.isdir("/data"):
        return "[error: no volume mounted at /data]"
    os.makedirs(WORKSPACE_BASE, exist_ok=True)
    if full is None:
        return "[error: invalid workspace path]"
    os.makedirs(os.path.dirname(full) or WORKSPACE_BASE, exist_ok=True)
    with open(full, "a", encoding="utf-8") as fh:
        fh.write(content)
    return full
