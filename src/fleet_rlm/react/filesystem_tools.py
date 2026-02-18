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
        base = Path(path)
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
            }

        # Directory: glob for files matching pattern
        matched = [p for p in base.glob(pattern) if p.is_file()]
        files = sorted(str(p.relative_to(base)) for p in matched)
        total_bytes = sum(p.stat().st_size for p in matched)

        return {
            "status": "ok",
            "path": str(base),
            "files": files[:100],  # Cap at 100 for display
            "count": len(files),
            "total_bytes": total_bytes,
            "hint": "Use load_document to read a specific file from this listing.",
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
