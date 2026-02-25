"""DSPy ReAct filesystem tools for the RLM chat agent.

This module contains filesystem navigation tools that allow the ReAct agent
to explore and read files on the host filesystem. Tools are defined as
standalone functions following the DSPy convention.

Tools included:
- list_files: List files on the host filesystem matching a glob pattern
- read_file_slice: Read a range of lines from a host file
- find_files: Search file contents on the host using regex pattern (ripgrep)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_filesystem_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build filesystem navigation tools with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.

    Args:
        agent: The RLMReActChatAgent instance to bind tools to.

    Returns:
        List of dspy.Tool objects for filesystem navigation.
    """
    from dspy import Tool

    def list_files(path: str = ".", pattern: str = "**/*") -> dict[str, Any]:
        """List files on the host filesystem matching a glob pattern.

        Returns files relative to the base path, with total count and size.

        Args:
            path: Base directory path to search. Defaults to current directory.
            pattern: Glob pattern to match files. Defaults to all files.

        Returns:
            Dictionary with status, path, files list, count, and total bytes.
        """
        base = Path(path).resolve()
        if not base.exists():
            raise FileNotFoundError(f"Path not found: {base}")
        if not base.is_dir():
            # Single file: return it as a 1-item list
            return {
                "status": "ok",
                "path": str(base),
                "files": [base.name],
                "count": 1,
                "total_bytes": base.stat().st_size,
                "list_files_scoped": False,
                "list_files_scope_roots": [],
            }

        IGNORED_DIRS = {
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
            return not any(part in IGNORED_DIRS for part in rel_parts)

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
                scoped_matches = {
                    candidate
                    for root in scope_roots
                    for candidate in root.glob(pattern_norm)
                    if _is_included(candidate)
                }
                matched_files = sorted(scoped_matches, key=str)

        if not matched_files:
            matched_files = [
                candidate
                for candidate in base.glob(pattern_norm)
                if _is_included(candidate)
            ]

        files_result = sorted(str(p.relative_to(base)) for p in matched_files)
        total_bytes = sum(p.stat().st_size for p in matched_files)
        scope_roots_rel = [str(root.relative_to(base)) for root in scope_roots]

        return {
            "status": "ok",
            "path": str(base),
            "files": files_result[:100],  # Cap at 100 for display
            "count": len(files_result),
            "total_bytes": total_bytes,
            "hint": "Use load_document to read a specific file from this listing.",
            "list_files_scoped": bool(scope_roots_rel),
            "list_files_scope_roots": scope_roots_rel,
        }

    def read_file_slice(
        path: str, start_line: int = 1, num_lines: int = 100
    ) -> dict[str, Any]:
        """Read a range of lines from a host file without loading the full document.

        Useful for inspecting large files. Line numbers are 1-indexed.

        Args:
            path: File path on the host filesystem.
            start_line: First line to read (1-indexed). Defaults to 1.
            num_lines: Number of lines to read. Defaults to 100.

        Returns:
            Dictionary with status, path, lines, and line counts.
        """
        from .document_tools import _read_document_content

        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if file_path.is_dir():
            raise IsADirectoryError(f"Cannot read lines from directory: {file_path}")

        content, _ = _read_document_content(file_path)
        lines = content.splitlines()
        total_lines = len(lines)

        # Adjust to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + num_lines)

        slice_lines = lines[start_idx:end_idx]
        numbered = [
            {"line": start_idx + i + 1, "text": text}
            for i, text in enumerate(slice_lines)
        ]

        return {
            "status": "ok",
            "path": str(file_path),
            "start_line": start_line,
            "lines": numbered,
            "returned_count": len(numbered),
            "total_lines": total_lines,
        }

    def find_files(pattern: str, path: str = ".", include: str = "") -> dict[str, Any]:
        """Search file contents on the host using regex pattern (ripgrep).

        Returns matching files with line numbers and text snippets.
        Use 'include' to filter by file extension (e.g., '*.py').

        Args:
            pattern: Regex pattern to search for in file contents.
            path: Base directory path to search. Defaults to current directory.
            include: File extension filter (e.g., '*.py'). Defaults to all files.

        Returns:
            Dictionary with status, pattern, search path, and matching hits.
        """
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
            "hits": hits[:20],  # Cap display at 20
        }

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
