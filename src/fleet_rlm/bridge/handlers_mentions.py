"""File/path mention search handler for bridge frontends."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

CACHE_TTL_SECONDS = 5.0
MAX_INDEX_ENTRIES = 20_000
MAX_QUERY_SCAN = 5_000
IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

_INDEX_CACHE: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def search_mentions(params: dict[str, Any]) -> dict[str, Any]:
    """Return ranked file/path suggestions for `@` mention UX."""
    query = str(params.get("query", "")).strip().lstrip("@")
    limit = int(params.get("limit", 30) or 30)
    root_param = str(params.get("root", "")).strip()
    root = Path(root_param).expanduser().resolve() if root_param else Path.cwd()

    if not root.exists() or not root.is_dir():
        return {"items": [], "query": query, "root": str(root), "count": 0}

    bounded_limit = max(1, min(limit, 200))
    items = _rank_matches(root=root, query=query, limit=bounded_limit)
    return {"items": items, "query": query, "root": str(root), "count": len(items)}


def _rank_matches(*, root: Path, query: str, limit: int) -> list[dict[str, Any]]:
    if not query:
        return _top_level_matches(root=root, limit=limit)

    scope = _resolve_scope_query(root=root, query=query)
    if scope is not None:
        scope_root, needle = scope
        entries = _walk_entries(
            scope_root=scope_root,
            root=root,
            max_entries=MAX_QUERY_SCAN,
        )
        return _rank_from_entries(entries=entries, needle=needle, limit=limit)

    entries = _get_cached_index(root)
    return _rank_from_entries(entries=entries, needle=query.lower(), limit=limit)


def _top_level_matches(*, root: Path, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    entries = sorted(
        root.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())
    )
    for entry in entries:
        if _skip_name(entry.name):
            continue
        kind = "dir" if entry.is_dir() else "file"
        rel = entry.name
        score = _score(rel_l=rel.lower(), needle="", kind=kind)
        results.append(
            {
                "path": rel + ("/" if kind == "dir" else ""),
                "kind": kind,
                "score": score,
            }
        )
        if len(results) >= limit:
            break
    return results


def _resolve_scope_query(*, root: Path, query: str) -> tuple[Path, str] | None:
    if "/" not in query:
        return None

    query_text = query.strip("/")
    if not query_text:
        return None

    if query.endswith("/"):
        prefix = query.rstrip("/")
        needle = ""
    else:
        prefix, needle = query.rsplit("/", 1)

    scope_root = (root / prefix).resolve()
    if not _is_within_root(path=scope_root, root=root):
        return None
    if not scope_root.exists() or not scope_root.is_dir():
        return None

    return scope_root, needle.lower()


def _is_within_root(*, path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _get_cached_index(root: Path) -> list[tuple[str, str]]:
    key = str(root)
    now = time.monotonic()
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        cached_at, entries = cached
        if now - cached_at <= CACHE_TTL_SECONDS:
            return entries

    entries = _build_index(root)
    _INDEX_CACHE[key] = (now, entries)
    return entries


def _build_index(root: Path) -> list[tuple[str, str]]:
    return _walk_entries(scope_root=root, root=root, max_entries=MAX_INDEX_ENTRIES)


def _walk_entries(
    *,
    scope_root: Path,
    root: Path,
    max_entries: int,
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []

    for current_dir, dirs, files in os.walk(scope_root, topdown=True):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not _skip_name(d))
        filtered_files = sorted(f for f in files if not _skip_name(f))

        current_path = Path(current_dir)
        for name in dirs:
            rel = (current_path / name).relative_to(root).as_posix()
            entries.append((rel, "dir"))
            if len(entries) >= max_entries:
                return entries

        for name in filtered_files:
            rel = (current_path / name).relative_to(root).as_posix()
            entries.append((rel, "file"))
            if len(entries) >= max_entries:
                return entries

    return entries


def _rank_from_entries(
    *,
    entries: list[tuple[str, str]],
    needle: str,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[tuple[int, str, str]] = []
    scanned = 0

    for rel, kind in entries:
        scanned += 1
        if scanned > MAX_QUERY_SCAN:
            break

        rel_l = rel.lower()
        if needle and needle not in rel_l:
            continue

        ranked.append((_score(rel_l=rel_l, needle=needle, kind=kind), rel, kind))

    ranked.sort(key=lambda item: (item[0], item[1]))
    return [
        {
            "path": rel + ("/" if kind == "dir" else ""),
            "kind": kind,
            "score": score,
        }
        for score, rel, kind in ranked[:limit]
    ]


def _score(*, rel_l: str, needle: str, kind: str) -> int:
    if not needle:
        score = 100 + (1 if kind == "file" else 0)
    elif rel_l.startswith(needle):
        score = 0
    else:
        score = rel_l.find(needle) + 10
    if kind == "dir":
        score += 3
    return score


def _skip_name(name: str) -> bool:
    return name.startswith(".")
