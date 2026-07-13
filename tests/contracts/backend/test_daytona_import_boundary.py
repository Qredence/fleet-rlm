"""K-003: Daytona SDK import boundary for fleet_rlm."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm"
ALLOWED_DAYTONA_IMPORT_ROOTS = {
    PACKAGE_ROOT / "daytona",
}


def _imported_roots(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    return imported


def test_only_daytona_package_imports_daytona_sdk() -> None:
    violators: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if "daytona" not in _imported_roots(tree):
            continue
        if any(path.is_relative_to(root) for root in ALLOWED_DAYTONA_IMPORT_ROOTS):
            continue
        violators.append(str(path.relative_to(PACKAGE_ROOT)))
    assert violators == [], f"Daytona SDK imports outside daytona/: {violators}"


def test_build_daytona_client_is_lazy() -> None:
    """client module may import Daytona types, but must not construct at import."""
    import fleet_rlm.daytona.client as client_module

    source = Path(client_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Expr | ast.Assign | ast.AnnAssign):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                assert name != "Daytona", "Daytona() must not run at module import time"
