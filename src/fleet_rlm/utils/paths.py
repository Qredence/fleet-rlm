"""Path utilities shared across the codebase."""

from __future__ import annotations


def is_local_path(path: str) -> bool:
    """Return True if path refers to a local filesystem location (not a URL)."""
    stripped = path.strip()
    return bool(stripped) and not stripped.startswith(("http://", "https://"))


def dedupe_paths(paths: list[str]) -> list[str]:
    """Deduplicate and normalize a list of paths, preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in paths:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered
