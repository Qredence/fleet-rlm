"""Import-safe package construction and secret-excluding settings."""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def test_package_imports_without_network() -> None:
    """Importing the clean package must not open sockets."""
    original_socket = socket.socket
    opened: list[Any] = []

    def guarded_socket(*args: Any, **kwargs: Any) -> Any:
        opened.append((args, kwargs))
        return original_socket(*args, **kwargs)

    socket.socket = guarded_socket  # type: ignore[method-assign, assignment]
    try:
        import fleet_rlm
        from fleet_rlm.config.settings import Settings

        assert fleet_rlm.__version__
        settings = Settings()
        assert settings.app_name
        assert opened == [], f"unexpected sockets during import/settings: {opened}"
    finally:
        socket.socket = original_socket  # type: ignore[method-assign, assignment]


def test_settings_exclude_secrets_from_serialization() -> None:
    """Secret fields must not appear as plaintext in public dumps."""
    from fleet_rlm.config.settings import Settings

    settings = Settings(
        daytona_api_key="super-secret-daytona",
        llm_api_key="super-secret-llm",
    )
    dumped = settings.model_dump(mode="json")
    dumped_str = str(dumped)

    assert "super-secret-daytona" not in dumped_str
    assert "super-secret-llm" not in dumped_str
    assert "daytona_api_key" not in dumped or dumped.get("daytona_api_key") in {
        None,
        "",
        "**********",
    }
    assert "llm_api_key" not in dumped or dumped.get("llm_api_key") in {
        None,
        "",
        "**********",
    }


def test_composition_common_import_does_not_configure_dspy_providers() -> None:
    """Importing composition.live alone must not configure a DSPy provider LM."""
    script = "import fleet_rlm.app_lifecycle\nimport dspy\nassert dspy.settings.lm is None, repr(dspy.settings.lm)\n"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr


def test_create_app_returns_fastapi_without_side_effects(monkeypatch) -> None:
    """create_app must return a FastAPI instance without constructing clients."""
    from fastapi import FastAPI

    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona-bench")
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title


def test_generic_runtime_modules_do_not_import_daytona_implementations() -> None:
    root = Path("src/fleet_rlm")
    candidates = [
        path
        for package in ("artifacts", "skills", "workspace", "attachments", "turns")
        for path in (root / package).glob("*.py")
    ]
    for shim in ("turns.py", "turn_preparation.py", "turn_settlement.py"):
        shim_path = root / shim
        if shim_path.exists():
            candidates.append(shim_path)

    # Workspace storage's agent transport and host I/O bridge are the two
    # narrow provider edges; domain policy stays in Workspace.
    provider_edges = {
        root / "workspace" / "storage.py",
        root / "workspace" / "host_io.py",
    }
    violations = [
        str(path)
        for path in candidates
        if path not in provider_edges and "fleet_rlm.daytona" in path.read_text(encoding="utf-8")
    ]

    assert violations == []


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = ("src/fleet_rlm", "tests", "scripts")
_LEGACY_ENGINE_NAMES = frozenset({"LegacyEngine", "AsyncLegacyEngine", "complete_legacy"})


def _dspy_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    dspy_names = {"dspy"}
    bare_baselm: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dspy_names.update(alias.asname or alias.name for alias in node.names if alias.name == "dspy")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "dspy" or module.startswith("dspy."):
                bare_baselm.update(alias.asname or alias.name for alias in node.names if alias.name == "BaseLM")
    return dspy_names, bare_baselm


def _legacy_baselm_subclasses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dspy_names, bare_baselm = _dspy_aliases(tree)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            from_dspy_attribute = (
                isinstance(base, ast.Attribute)
                and base.attr == "BaseLM"
                and isinstance(base.value, ast.Name)
                and base.value.id in dspy_names
            )
            from_dspy_import = isinstance(base, ast.Name) and base.id in bare_baselm
            if from_dspy_attribute or from_dspy_import:
                offenders.append(f"{path}:{node.lineno}: class {node.name}")
    return offenders


def _forward_contract_declarations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "forward_contract":
                offenders.append(f"{path}:{node.lineno}: forward_contract")
            elif isinstance(target, ast.Attribute) and target.attr == "forward_contract":
                offenders.append(f"{path}:{node.lineno}: .forward_contract")
    return offenders


def _legacy_engine_references(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in _LEGACY_ENGINE_NAMES or (alias.asname or "") in _LEGACY_ENGINE_NAMES:
                    offenders.append(f"{path}:{node.lineno}: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _LEGACY_ENGINE_NAMES:
            offenders.append(f"{path}:{node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _LEGACY_ENGINE_NAMES:
            offenders.append(f"{path}:{node.lineno}: .{node.attr}")
    return offenders


def _scan(collector) -> list[str]:
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            offenders.extend(collector(path))
    return offenders


@pytest.mark.parametrize("root", _SCAN_ROOTS)
def test_no_module_subclasses_dspy_baselm(root: str) -> None:
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / root).rglob("*.py")):
        offenders.extend(_legacy_baselm_subclasses(path))

    assert offenders == [], (
        "dspy.BaseLM subclasses are deprecated in DSPy 3.4 and removed in 3.5; "
        f"pass a complete(request) -> Response engine to dspy.LM(engine=...) instead: {offenders}"
    )


def test_no_module_declares_forward_contract() -> None:
    assert _scan(_forward_contract_declarations) == []


def test_no_module_references_legacy_engine() -> None:
    assert _scan(_legacy_engine_references) == []
