"""DSPy ReAct filesystem tools for the RLM chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..agent import RLMReActChatAgent


@dataclass(slots=True)
class _FilesystemToolContext:
    """Shared tool context for host filesystem operations."""

    agent: "RLMReActChatAgent"


def _list_files_impl(
    _ctx: _FilesystemToolContext, path: str = ".", pattern: str = "**/*"
) -> dict[str, Any]:
    """List files on the host filesystem matching a glob pattern."""
    base = Path(path).resolve()
    if not base.exists():
        raise FileNotFoundError(f"Path not found: {base}")
    if not base.is_dir():
        return {
            "status": "ok",
            "path": str(base),
            "files": [base.name],
            "count": 1,
            "total_bytes": base.stat().st_size,
            "list_files_scoped": False,
            "list_files_scope_roots": [],
        }

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

    def _is_included(candidate: Path) -> bool:
        if not candidate.is_file():
            return False
        rel_parts = candidate.relative_to(base).parts
        return not any(part in ignored_dirs for part in rel_parts)

    pattern_norm = (pattern or "**/*").strip() or "**/*"
    path_norm = path.strip() if isinstance(path, str) else "."
    is_default_root = path_norm in {"", ".", "./"}
    pattern_parts = Path(pattern_norm).parts
    first_part = pattern_parts[0] if pattern_parts else ""
    has_explicit_root = bool(first_part) and not any(
        token in first_part for token in ("*", "?", "[", "]")
    )
    source_first_scope = (
        is_default_root and not has_explicit_root and "**" in pattern_norm
    )

    scope_roots: list[Path] = []
    matched_files: list[Path] = []
    if source_first_scope:
        for root_name in ("src", "tests", "docs", "scripts"):
            root = base / root_name
            if root.exists() and root.is_dir():
                scope_roots.append(root)
        if scope_roots:
            scoped_matches: list[Path] = []
            for root in scope_roots:
                for candidate in root.glob(pattern_norm):
                    candidate_path = base / str(candidate)
                    if _is_included(candidate_path):
                        scoped_matches.append(candidate_path)
            for candidate in sorted(set(scoped_matches), key=str):
                if isinstance(candidate, Path):
                    matched_files.append(candidate)

    if not matched_files:
        for candidate in base.glob(pattern_norm):
            candidate_path = base / str(candidate)
            if _is_included(candidate_path):
                matched_files.append(candidate_path)

    files_result = sorted(str(p.relative_to(base)) for p in matched_files)
    total_bytes = sum(p.stat().st_size for p in matched_files)
    scope_roots_rel = [str(root.relative_to(base)) for root in scope_roots]

    return {
        "status": "ok",
        "path": str(base),
        "files": files_result[:100],
        "count": len(files_result),
        "total_bytes": total_bytes,
        "hint": "Use load_document to read a specific file from this listing.",
        "list_files_scoped": bool(scope_roots_rel),
        "list_files_scope_roots": scope_roots_rel,
    }


def _read_file_slice_impl(
    _ctx: _FilesystemToolContext,
    path: str,
    start_line: int = 1,
    num_lines: int = 100,
) -> dict[str, Any]:
    """Read a range of lines from a host file without loading the full document."""
    from .document import _read_document_content

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if file_path.is_dir():
        raise IsADirectoryError(f"Cannot read lines from directory: {file_path}")

    content, _ = _read_document_content(file_path)
    lines = content.splitlines()
    total_lines = len(lines)

    start_idx = max(0, start_line - 1)
    end_idx = min(total_lines, start_idx + num_lines)

    slice_lines = lines[start_idx:end_idx]
    numbered = [
        {"line": start_idx + i + 1, "text": text} for i, text in enumerate(slice_lines)
    ]

    return {
        "status": "ok",
        "path": str(file_path),
        "start_line": start_line,
        "lines": numbered,
        "returned_count": len(numbered),
        "total_lines": total_lines,
    }


def _find_files_impl(
    _ctx: _FilesystemToolContext, pattern: str, path: str = ".", include: str = ""
) -> dict[str, Any]:
    """Search file contents on the host using regex pattern (ripgrep)."""
    try:
        from ripgrepy import Ripgrepy  # ty: ignore[unresolved-import]
    except ImportError:
        return {
            "status": "error",
            "error": "ripgrepy not installed (install with 'interactive' extra)",
        }

    rg = Ripgrepy(pattern, path).json().with_filename().line_number().max_count(50)
    if include:
        rg = rg.glob(include)

    try:
        out = rg.run()
    except Exception as exc:
        return {
            "status": "error",
            "pattern": pattern,
            "path": path,
            "error": str(exc),
        }

    hits = []
    for item in out.as_dict:
        if item.get("type") != "match":
            continue
        data = item.get("data", {})
        path_text = data.get("path", {}).get("text", "")
        line_no = data.get("line_number")
        line_text = data.get("lines", {}).get("text", "").rstrip("\n")
        hits.append({"path": path_text, "line": line_no, "text": line_text})

    return {
        "status": "ok",
        "pattern": pattern,
        "search_path": path,
        "include": include or "all files",
        "count": len(hits),
        "hits": hits[:20],
    }


def build_filesystem_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build filesystem navigation tools with a shared context object."""
    from dspy import Tool

    ctx = _FilesystemToolContext(agent=agent)

    def list_files(path: str = ".", pattern: str = "**/*") -> dict[str, Any]:
        return _list_files_impl(ctx, path=path, pattern=pattern)

    def read_file_slice(
        path: str, start_line: int = 1, num_lines: int = 100
    ) -> dict[str, Any]:
        return _read_file_slice_impl(
            ctx,
            path=path,
            start_line=start_line,
            num_lines=num_lines,
        )

    def find_files(pattern: str, path: str = ".", include: str = "") -> dict[str, Any]:
        return _find_files_impl(ctx, pattern=pattern, path=path, include=include)

    return [
        Tool(
            list_files,
            name="list_files",
            desc="List files on the host filesystem matching a glob pattern",
        ),
        Tool(
            read_file_slice,
            name="read_file_slice",
            desc="Read a range of lines from a host file without loading the full document",
        ),
        Tool(
            find_files,
            name="find_files",
            desc="Search file contents on the host using regex pattern (ripgrep)",
        ),
    ]
