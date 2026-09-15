#!/usr/bin/env python3
"""Enforce the canonical backend package boundaries and nested-ternary clarity."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_walk import iter_imports, matches

SOURCE_ROOT = ROOT / "src"
PACKAGE = SOURCE_ROOT / "fleet_rlm"


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


def check_codebase_tree(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """Return boundary and clarity violation messages for the backend package."""
    root = root.resolve()
    source_root = root / "src"
    package = source_root / "fleet_rlm"
    boundary_violations: list[str] = []
    clarity_violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for line, imported in iter_imports(path, source_root=source_root):
            if (imported == "daytona" or imported.startswith("daytona.")) and (
                not relative.parts or relative.parts[0] != "daytona"
            ):
                boundary_violations.append(f"{relative}:{line}: Daytona SDK import outside daytona/")
            if relative.parts[:2] == ("api", "routes") and (
                matches(imported, "fleet_rlm.persistence") or matches(imported, "fleet_rlm.daytona")
            ):
                boundary_violations.append(f"{relative}:{line}: route bypasses injected application modules")
        for line in find_nested_ternaries(tree):
            clarity_violations.append(f"{relative}:{line}: nested conditional expression (IfExp)")
    return boundary_violations, clarity_violations


def main() -> int:
    boundary_violations, clarity_violations = check_codebase_tree()
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
