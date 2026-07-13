#!/usr/bin/env python3
"""Enforce the canonical backend package boundaries."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "fleet_rlm"


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def main() -> int:
    violations: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        for line, imported in _imports(path):
            if imported == "daytona" or imported.startswith("daytona."):
                if not relative.parts or relative.parts[0] != "daytona":
                    violations.append(f"{relative}:{line}: Daytona SDK import outside daytona/")
            if relative.parts[:2] == ("api", "routes") and imported.startswith(
                ("fleet_rlm.persistence", "fleet_rlm.daytona")
            ):
                violations.append(f"{relative}:{line}: route bypasses injected application modules")

    if violations:
        print("Backend boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("Canonical backend boundaries pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
