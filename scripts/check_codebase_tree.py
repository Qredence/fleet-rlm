#!/usr/bin/env python3
"""Enforce import boundaries defined in the codebase map.

This script checks that packages respect their documented import rules:
- Backend `runtime/` may not import from `api.routers`
- Backend `quality/` may not import from `api/`
- Frontend `features/**` and `components/**` must not import from `src/fleet_rlm/**`
- Frontend `features/**` and `components/**` must import backend types through `lib/rlm-api/`

Usage:
    python scripts/check_codebase_tree.py
    make check-codebase-tree
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Backend root
BACKEND_ROOT = Path("src/fleet_rlm")

# Frontend root
FRONTEND_ROOT = Path("src/frontend/src")

# Patterns to exempt from scanning (directory names and specific file paths).
# These are generated artifacts, out-of-scope packages, test directories,
# and build outputs that would produce noise or false positives (VAL-D-017).
EXEMPT_DIR_NAMES = {
    "tests",
    "test",
    "__tests__",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "scaffold",  # Out-of-scope package (skill authoring reference)
}

EXEMPT_PATH_SUFFIXES = {
    "src/fleet_rlm/ui/dist",  # Packaged UI assets (generated)
    "src/frontend/src/routeTree.gen.ts",  # TanStack Router generated route tree
    "src/frontend/src/lib/rlm-api/generated",  # OpenAPI generated client
}


# TypeScript import patterns (ESM)
TS_IMPORT_RE = re.compile(
    r"""
    (?:
        import\s+(?:(?:type\s+)?(?:{[^}]*}|\*\s+as\s+\w+|\w+)\s+from\s+)?  # import X from "..."
        |
        import\s*\(  # dynamic import("...")
    )
    ['"]([^'"]+)['"]
    """,
    re.VERBOSE,
)


def is_exempt(path: Path) -> bool:
    """Check if a path should be exempt from scanning.

    Exempts files inside test directories, generated artifacts, and build outputs.
    Uses directory component matching, not substring matching, to avoid false exemptions.
    """
    path_str = str(path)

    # Check for exempt directory names in path components
    for part in path.parts:
        if part in EXEMPT_DIR_NAMES:
            return True

    # Check for specific path suffixes (generated files, dist dirs)
    # Use component-aware suffix matching: match if the path ends with the suffix
    # (as a full path component), not if the suffix appears as a substring.
    return any(path_str.endswith(suffix) for suffix in EXEMPT_PATH_SUFFIXES)


def resolve_relative_import(file_path: Path, backend_root: Path, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute module path.

    Args:
        file_path: The file containing the import
        backend_root: The backend package root (src/fleet_rlm)
        level: Number of dots (1 for ., 2 for .., etc.)
        module: The module being imported (may be None for 'from . import X')

    Returns:
        The resolved absolute module path, or None if resolution fails
    """
    try:
        # Get the directory containing the file
        current_dir = file_path.parent

        # Go up 'level' directories (level=1 means current package, level=2 means parent, etc.)
        for _ in range(level - 1):
            current_dir = current_dir.parent

        # Convert the directory to a module path
        rel_to_root = current_dir.relative_to(backend_root.parent)
        base_module = ".".join(rel_to_root.parts)

        # Append the module if specified
        if module:
            return f"{base_module}.{module}" if base_module else module
        return base_module
    except (ValueError, Exception):
        # If we can't resolve, return None
        return None


def extract_python_imports(file_path: Path, backend_root: Path) -> list[str]:
    """Extract import module names from a Python file.

    Args:
        file_path: Path to the Python file
        backend_root: The backend package root for resolving relative imports
    """
    try:
        code = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(code, filename=str(file_path))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import - resolve to absolute
                if node.module is None:
                    # "from . import X" or "from .. import X" —
                    # resolve each alias individually
                    for alias in node.names:
                        resolved = resolve_relative_import(file_path, backend_root, node.level, alias.name)
                        if resolved:
                            imports.append(resolved)
                else:
                    resolved = resolve_relative_import(file_path, backend_root, node.level, node.module)
                    if resolved:
                        imports.append(resolved)
            elif node.module:
                imports.append(node.module)
    return imports


def extract_ts_imports(file_path: Path) -> list[str]:
    """Extract import paths from a TypeScript/TSX file."""
    try:
        code = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    imports = []
    for match in TS_IMPORT_RE.finditer(code):
        imports.append(match.group(1))
    return imports


def check_backend_runtime_imports() -> list[str]:
    """Check that runtime/ does not import from api.routers.

    Per architecture.md §2.7: runtime/ may import api.events and api.config
    but NOT api.routers. This catches both absolute and relative imports.
    """
    violations = []
    runtime_dir = BACKEND_ROOT / "runtime"

    if not runtime_dir.exists():
        return violations

    for py_file in sorted(runtime_dir.rglob("*.py")):
        if is_exempt(py_file):
            continue

        imports = extract_python_imports(py_file, BACKEND_ROOT)
        for imp in imports:
            if imp.startswith("fleet_rlm.api.routers"):
                violations.append(f"{py_file}: imports {imp} (runtime/ may not import api.routers)")

    return violations


def check_backend_quality_imports() -> list[str]:
    """Check that quality/ does not import from api/ business logic.

    quality/ may import from api.schemas (data structures only) but not from
    api.routers, api.runtime_services, or other api business logic modules.
    """
    violations = []
    quality_dir = BACKEND_ROOT / "quality"

    if not quality_dir.exists():
        return violations

    # Allowed api subpackages for quality/ (data structures only)
    allowed_api_imports = {"fleet_rlm.api.schemas"}

    for py_file in sorted(quality_dir.rglob("*.py")):
        if is_exempt(py_file):
            continue

        imports = extract_python_imports(py_file, BACKEND_ROOT)
        for imp in imports:
            if imp.startswith("fleet_rlm.api"):
                # Check if it's an allowed import (api.schemas)
                is_allowed = any(imp == allowed or imp.startswith(allowed + ".") for allowed in allowed_api_imports)
                if not is_allowed:
                    violations.append(
                        f"{py_file}: imports {imp} (quality/ may only import api.schemas, not api business logic)"
                    )

    return violations


def check_frontend_no_backend_imports() -> list[str]:
    """Check that features/ and components/ do not import from src/fleet_rlm/.

    Per architecture.md §2.7: frontend callers in features/ and components/
    must import backend types through lib/rlm-api/ only. Direct imports
    into src/fleet_rlm/ are forbidden (typo guard).
    """
    violations = []

    for subdir in sorted(["features", "components"]):
        check_dir = FRONTEND_ROOT / subdir
        if not check_dir.exists():
            continue

        for ts_file in sorted(check_dir.rglob("*.ts")):
            if is_exempt(ts_file):
                continue

            imports = extract_ts_imports(ts_file)
            for imp in imports:
                # Check for direct backend imports (both ESM and dynamic)
                if "fleet_rlm" in imp or imp.startswith("../../fleet_rlm"):
                    violations.append(f"{ts_file}: imports {imp} (must use lib/rlm-api/ for backend types)")

        for tsx_file in sorted(check_dir.rglob("*.tsx")):
            if is_exempt(tsx_file):
                continue

            imports = extract_ts_imports(tsx_file)
            for imp in imports:
                if "fleet_rlm" in imp or imp.startswith("../../fleet_rlm"):
                    violations.append(f"{tsx_file}: imports {imp} (must use lib/rlm-api/ for backend types)")

    return violations


def main() -> int:
    """Run all codebase tree checks."""
    all_violations = []

    print("Checking backend runtime/ import boundaries...")
    all_violations.extend(check_backend_runtime_imports())

    print("Checking backend quality/ import boundaries...")
    all_violations.extend(check_backend_quality_imports())

    print("Checking frontend features/ and components/ import boundaries...")
    all_violations.extend(check_frontend_no_backend_imports())

    if all_violations:
        print("\nCodebase tree violations found:\n")
        for violation in all_violations:
            print(f"  ❌ {violation}")
        print(f"\nTotal violations: {len(all_violations)}")
        return 1

    print("\n✓ All codebase tree checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
