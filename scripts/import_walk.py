"""Shared AST import extraction for repository boundary checkers."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path


def module_name(path: Path, source_root: Path) -> tuple[str, ...]:
    """Return the package module parts for a source file."""
    return path.relative_to(source_root).with_suffix("").parts


def resolve_from_import(
    node: ast.ImportFrom,
    *,
    path: Path,
    source_root: Path,
) -> Iterable[str]:
    """Yield absolute module names represented by an ``ImportFrom`` node.

    Relative imports are resolved against the source file's package.  Both
    the imported base and alias are yielded so ``from fleet_rlm import chat``
    is checked just like ``import fleet_rlm.chat``.
    """
    module_parts = module_name(path, source_root)
    package_parts = module_parts[:-1]
    if node.level:
        anchor_length = max(0, len(package_parts) - (node.level - 1))
        base_parts = list(package_parts[:anchor_length])
        if node.module:
            base_parts.extend(node.module.split("."))
    else:
        base_parts = node.module.split(".") if node.module else []

    if not base_parts:
        return
    base = ".".join(base_parts)
    yield base
    for alias in node.names:
        if alias.name != "*":
            yield f"{base}.{alias.name}"


def iter_imports(path: Path, *, source_root: Path) -> Iterable[tuple[int, str]]:
    """Yield line-numbered absolute imports, including local imports."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            for imported in resolve_from_import(node, path=path, source_root=source_root):
                yield node.lineno, imported


def matches(imported: str, target: str) -> bool:
    """Match a module prefix, including the intentional trailing-underscore rule."""
    if target.endswith("_"):
        return imported.startswith(target)
    return imported == target or imported.startswith(f"{target}.")


__all__ = ["iter_imports", "matches", "module_name", "resolve_from_import"]
