#!/usr/bin/env python3
"""Enforce the canonical backend package boundaries and nested-ternary clarity."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "fleet_rlm"


def _imports_from_tree(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def find_nested_ternaries(tree: ast.AST) -> list[int]:
    """Return line numbers of ``ast.IfExp`` nodes that nest another ``IfExp``.

    A simple conditional expression (``a if cond else b``) is allowed. Nesting
    another ``IfExp`` anywhere under the body or else branch (including inside
    calls) is a clarity violation. Nesting only in the condition is allowed.
    """
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        for child in (node.body, node.orelse):
            if any(isinstance(sub, ast.IfExp) for sub in ast.walk(child)):
                violations.append(node.lineno)
                break
    return violations


def main() -> int:
    boundary_violations: list[str] = []
    clarity_violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for line, imported in _imports_from_tree(tree):
            if (imported == "daytona" or imported.startswith("daytona.")) and (
                not relative.parts or relative.parts[0] != "daytona"
            ):
                boundary_violations.append(f"{relative}:{line}: Daytona SDK import outside daytona/")
            if relative.parts[:2] == ("api", "routes") and imported.startswith(
                (
                    "fleet_rlm.persistence",
                    "fleet_rlm.daytona",
                )
            ):
                boundary_violations.append(f"{relative}:{line}: route bypasses injected application modules")
        for line in find_nested_ternaries(tree):
            clarity_violations.append(f"{relative}:{line}: nested conditional expression (IfExp)")

    if boundary_violations or clarity_violations:
        print("Backend tree check failed:", file=sys.stderr)
        if boundary_violations:
            print("Boundary:", file=sys.stderr)
            for violation in boundary_violations:
                print(f"- {violation}", file=sys.stderr)
        if clarity_violations:
            print("Clarity:", file=sys.stderr)
            for violation in clarity_violations:
                print(f"- {violation}", file=sys.stderr)
        return 1
    print("Canonical backend tree check passed (boundaries + nested-ternary clarity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
