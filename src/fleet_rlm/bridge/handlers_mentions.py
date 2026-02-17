"""File/path mention search handler for bridge frontends."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def search_mentions(params: dict[str, Any]) -> dict[str, Any]:
    """Return ranked file/path suggestions for `@` mention UX."""
    query = str(params.get("query", "")).strip().lstrip("@")
    limit = int(params.get("limit", 30) or 30)
    root_param = str(params.get("root", "")).strip()
    root = Path(root_param).expanduser().resolve() if root_param else Path.cwd()

    if not root.exists() or not root.is_dir():
        return {"items": [], "query": query, "root": str(root), "count": 0}

    items = _rank_matches(root=root, query=query, limit=max(1, min(limit, 200)))
    return {"items": items, "query": query, "root": str(root), "count": len(items)}


def _rank_matches(*, root: Path, query: str, limit: int) -> list[dict[str, Any]]:
    needle = query.lower()
    ranked: list[tuple[int, str, Path]] = []

    # Keep this bounded for responsiveness on very large repositories.
    scanned = 0
    for path in root.rglob("*"):
        scanned += 1
        if scanned > 5000:
            break
        if path.name.startswith("."):
            continue

        rel = path.relative_to(root).as_posix()
        rel_l = rel.lower()
        if needle and needle not in rel_l:
            continue

        if not needle:
            score = 100 + (1 if path.is_file() else 0)
        elif rel_l.startswith(needle):
            score = 0
        else:
            score = rel_l.find(needle) + 10
        if path.is_dir():
            score += 3
        ranked.append((score, rel, path))

    ranked.sort(key=lambda item: (item[0], item[1]))
    output: list[dict[str, Any]] = []
    for score, rel, path in ranked[:limit]:
        output.append(
            {
                "path": rel + ("/" if path.is_dir() else ""),
                "kind": "dir" if path.is_dir() else "file",
                "score": score,
            }
        )
    return output
