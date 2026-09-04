#!/usr/bin/env python3
"""Enforce Phase-0 architecture vocabulary and DSPy tool-name ownership."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "fleet_rlm"
ADR = ROOT / "docs" / "architecture" / "001-execution-state.md"
REQUIRED_TERMS = (
    "Workspace",
    "Session",
    "Turn",
    "Run",
    "RunClaim",
    "SessionSandbox",
    "InterpreterContext",
    "ChildEnvironment",
)
RESERVED_DSPY_BUILTINS = {"llm_query", "llm_query_batched"}


def main() -> int:
    errors: list[str] = []
    text = ADR.read_text(encoding="utf-8") if ADR.is_file() else ""
    for term in REQUIRED_TERMS:
        if term not in text:
            errors.append(f"execution-state ADR is missing canonical term: {term}")
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in RESERVED_DSPY_BUILTINS:
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: Fleet may not implement {node.name}")
    if errors:
        print("Architecture vocabulary check failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print("Architecture vocabulary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
