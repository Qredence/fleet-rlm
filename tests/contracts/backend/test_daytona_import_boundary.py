"""Daytona SDK import and lazy-client construction contracts."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm"
ALLOWED_DAYTONA_IMPORT_ROOTS = {
    PACKAGE_ROOT / "daytona",
}
EXPECTED_DAYTONA_MODULES = {
    "__init__.py",
    "broker_source.py",
    "diagnostics.py",
    "dspy_sync_bridge.py",
    "errors.py",
    "http_broker.py",
    "interpreter.py",
    "interpreter_output.py",
    "lifecycle.py",
    "optimization_evaluator.py",
    "platform.py",
    "provisioning.py",
    "recursive_child_runtime.py",
    "run_environment.py",
    "session_manager.py",
    "workspace_agent.py",
    "workspace_agent_runtime.py",
    "workspace_fs.py",
    "workspace_gateway.py",
    "workspace_memory.py",
}


def test_daytona_package_has_exact_simplified_module_boundary() -> None:
    actual = {path.name for path in (PACKAGE_ROOT / "daytona").glob("*.py")}
    assert actual == EXPECTED_DAYTONA_MODULES


def _imported_roots(tree: ast.AST) -> set[str]:
    """
    Extract the top-level module names referenced by import statements in an abstract syntax tree.

    Parameters:
        tree (ast.AST): The syntax tree to inspect.

    Returns:
        set[str]: The set of imported top-level module names.
    """
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    return imported


def _imported_modules(tree: ast.AST) -> set[str]:
    """Collect fully qualified module names imported by an abstract syntax tree.

    Parameters:
        tree (ast.AST): The syntax tree to inspect.

    Returns:
        set[str]: The imported module names.
    """
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
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


def test_rlm_recursive_executor_uses_provider_neutral_child_runtime_contract() -> None:
    path = PACKAGE_ROOT / "rlm" / "recursive_calls.py"
    imports = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
    assert "fleet_rlm.daytona.recursive_child_runtime" not in imports


def test_child_runtime_cleanup_and_authorization_errors_have_provider_neutral_identity() -> None:
    from fleet_rlm.daytona.recursive_child_runtime import (
        ChildRuntimeAuthorizationError as DaytonaAuthorizationError,
    )
    from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeCleanupError as DaytonaCleanupError
    from fleet_rlm.rlm.child_runtime import (
        ChildRuntimeAuthorizationError,
        ChildRuntimeCleanupError,
    )

    assert DaytonaAuthorizationError is ChildRuntimeAuthorizationError
    assert DaytonaCleanupError is ChildRuntimeCleanupError


def test_build_daytona_client_is_lazy() -> None:
    """Platform module may import Daytona types, but must not construct at import."""
    import fleet_rlm.daytona.platform as platform_module

    source = Path(platform_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Expr | ast.Assign | ast.AnnAssign):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                assert name != "Daytona", "Daytona() must not run at module import time"
