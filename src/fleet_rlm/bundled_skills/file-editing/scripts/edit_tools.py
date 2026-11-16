"""Dual-mode unique-match file editing, sequential batch edits, and unified diff helpers."""

from __future__ import annotations

import argparse
import difflib
import errno
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _resolve_file(path: str | Path) -> Path:
    """Resolve an existing non-symlink regular file path."""
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        ws_root = Path(os.environ.get("FLEET_WORKSPACE", "/workspace"))
        ws_candidate = ws_root / candidate
        if ws_candidate.exists():
            candidate = ws_candidate
    if not candidate.exists():
        raise FileNotFoundError(f"Target file not found: {path}")
    if candidate.is_symlink():
        raise ValueError(f"Target path may not be a symbolic link: {path}")
    if not candidate.is_file():
        raise ValueError(f"Target path is not a regular file: {path}")
    return candidate


def _read_exact_text(target: Path) -> str:
    """Read UTF-8 text preserving exact line endings."""
    with target.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _atomic_write_text(target: Path, content: str) -> None:
    """Write text via a sibling temporary file and replace it onto the target.

    The original permission bits are copied onto the temporary file first, because mkstemp
    creates it as 0600. If the rename is refused (EXDEV, ENOSYS, EOPNOTSUPP), the content is
    written directly into the target instead; that fallback is not atomic.
    """
    raw = content.encode("utf-8")
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copymode(target, tmp_path)
        try:
            os.replace(tmp_path, target)
        except OSError as exc:
            if exc.errno not in {errno.ENOSYS, errno.EXDEV, errno.EOPNOTSUPP}:
                raise
            with target.open("wb") as handle:
                handle.write(raw)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _unified_diff(target: Path, original: str, updated: str, *, context_lines: int = 3) -> str:
    """Return a unified diff between original and updated text."""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
            n=context_lines,
        )
    )


def _replace_unique(content: str, old_str: str, new_str: str) -> tuple[str, int, int]:
    """Replace an exact single occurrence of old_str and return (updated, start_line, end_line)."""
    if not isinstance(old_str, str) or not old_str:
        raise ValueError("old_str must be a non-empty string")
    if not isinstance(new_str, str):
        raise ValueError("new_str must be a string")

    occurrences = content.count(old_str)
    if occurrences == 0:
        raise ValueError("old_str occurred 0 times in target file; no match found")
    if occurrences > 1:
        raise ValueError(f"old_str occurred {occurrences} times in target file; expected exactly 1 unique match")

    index = content.index(old_str)
    start_line = content[:index].count("\n") + 1
    line_span = len(new_str.splitlines()) if new_str else 1
    end_line = start_line + max(1, line_span) - 1
    updated = content[:index] + new_str + content[index + len(old_str) :]
    return updated, start_line, end_line


def preview_diff(path: str | Path, new_content: str, *, context_lines: int = 3) -> str:
    """Render a unified diff comparing an existing file against proposed content.

    Args:
        path: Existing file path.
        new_content: Proposed full replacement text.
        context_lines: Number of unchanged context lines around each hunk.

    Returns:
        Unified diff string (empty when content is unchanged).
    """
    if not isinstance(new_content, str):
        raise ValueError("new_content must be a string")
    if context_lines < 0:
        raise ValueError("context_lines must be non-negative")
    target = _resolve_file(path)
    original = _read_exact_text(target)
    return _unified_diff(target, original, new_content, context_lines=context_lines)


def edit_unique(
    path: str | Path,
    old_str: str,
    new_str: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replace one unique substring in a file and return line bounds and diff.

    Args:
        path: Target file path.
        old_str: Non-empty exact substring occurring exactly once in the file.
        new_str: Replacement substring.
        dry_run: When True, compute diff and validate uniqueness without writing.

    Returns:
        Dictionary with ``path``, ``start_line``, ``end_line``, ``diff``, and ``dry_run``.
    """
    target = _resolve_file(path)
    original = _read_exact_text(target)
    updated, start_line, end_line = _replace_unique(original, old_str, new_str)
    diff = _unified_diff(target, original, updated)
    if not dry_run:
        _atomic_write_text(target, updated)
    return {
        "path": str(target),
        "start_line": start_line,
        "end_line": end_line,
        "diff": diff,
        "dry_run": dry_run,
    }


def apply_edits(
    path: str | Path,
    edits: Iterable[tuple[str, str] | list[str] | dict[str, str]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a sequence of unique-match edits atomically after validating all in memory.

    Args:
        path: Target file path.
        edits: Sequence of ``(old_str, new_str)`` pairs or ``{"old_str", "new_str"}`` dicts.
        dry_run: When True, validate and preview the combined diff without modifying disk.

    Returns:
        Dictionary summarizing applied edit count, line ranges, unified diff, and ``dry_run``.
    """
    target = _resolve_file(path)
    edit_list = list(edits)
    if not edit_list:
        raise ValueError("edits must contain at least one replacement")

    original = _read_exact_text(target)
    current = original
    ranges: list[dict[str, int]] = []

    for idx, item in enumerate(edit_list):
        if isinstance(item, dict):
            if "old_str" not in item or "new_str" not in item:
                raise ValueError(f"Edit {idx} dict must contain 'old_str' and 'new_str' keys")
            old_str, new_str = item["old_str"], item["new_str"]
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            old_str, new_str = item[0], item[1]
        else:
            raise ValueError(f"Edit {idx} must be a (old_str, new_str) pair or dict")
        try:
            current, start_line, end_line = _replace_unique(current, old_str, new_str)
        except ValueError as exc:
            raise ValueError(f"Edit {idx} failed: {exc}") from exc
        ranges.append({"index": idx, "start_line": start_line, "end_line": end_line})

    diff = _unified_diff(target, original, current)
    if not dry_run:
        _atomic_write_text(target, current)
    return {
        "path": str(target),
        "edits_applied": len(ranges),
        "ranges": ranges,
        "diff": diff,
        "dry_run": dry_run,
    }


def run(
    path: str | Path,
    old_str: str,
    new_str: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delegate default callable module invocation to ``edit_unique``.

    Args:
        path: Target file path.
        old_str: Non-empty exact substring occurring exactly once in the file.
        new_str: Replacement substring.
        dry_run: When True, preview the change without writing.

    Returns:
        Result dictionary from ``edit_unique``.
    """
    return edit_unique(path, old_str, new_str, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint for unique-match file editing."""
    parser = argparse.ArgumentParser(description="Apply a unique-match replacement to a file.")
    parser.add_argument("--path", required=True, help="Target file path.")
    parser.add_argument("--old-str", required=True, help="Exact substring occurring once.")
    parser.add_argument("--new-str", required=True, help="Replacement string.")
    parser.add_argument("--dry-run", action="store_true", help="Preview diff without modifying file.")

    args = parser.parse_args(argv)
    result = edit_unique(args.path, args.old_str, args.new_str, dry_run=args.dry_run)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
