"""Unit tests for scripts/check_architecture.py: tree layout, imports, and boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.check_architecture import (
    deps_check_dependency_boundaries as check_dependency_boundaries,
)
from scripts.check_architecture import (
    main,
)
from scripts.check_architecture import (
    tree_check_codebase_tree as check_codebase_tree,
)
from scripts.check_architecture import (
    tree_check_test_layout as check_test_layout,
)
from scripts.check_architecture import (
    walker_iter_imports as iter_imports,
)
from scripts.check_architecture import (
    walker_matches as matches,
)
from scripts.check_architecture import (
    walker_resolve_from_import as resolve_from_import,
)


def _write_src(root: Path, relative: str, source: str) -> None:
    path = root / "src" / "fleet_rlm" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _write_test(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


# --- Import Walker Tests ---


def test_matches_trailing_underscore_prefix_rule() -> None:
    assert matches("fleet_rlm.persistence_", "fleet_rlm.persistence_")
    assert matches("fleet_rlm.persistence_models", "fleet_rlm.persistence_")
    assert not matches("fleet_rlm.persistence", "fleet_rlm.persistence_")


def test_resolve_from_import_absolute_and_alias() -> None:
    source_root = Path("/repo/src")
    path = source_root / "fleet_rlm" / "api" / "routes" / "sessions.py"
    node = ast.parse("from fleet_rlm.persistence import models as persistence_models").body[0]
    assert isinstance(node, ast.ImportFrom)

    resolved = list(resolve_from_import(node, path=path, source_root=source_root))
    assert resolved == ["fleet_rlm.persistence", "fleet_rlm.persistence.models"]


def test_iter_imports_yields_line_numbers(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    package = source_root / "fleet_rlm"
    package.mkdir(parents=True)
    path = package / "example.py"
    path.write_text(
        "import fleet_rlm.chat\nfrom fleet_rlm.persistence import models\n",
        encoding="utf-8",
    )

    imports = list(iter_imports(path, source_root=source_root))
    assert imports == [
        (1, "fleet_rlm.chat"),
        (2, "fleet_rlm.persistence"),
        (2, "fleet_rlm.persistence.models"),
    ]


# --- Codebase Tree & Test Layout Tests ---


def test_relative_daytona_route_import_is_reported(tmp_path: Path) -> None:
    _write_src(tmp_path, "daytona/provider.py", "def provider() -> None: ...\n")
    _write_src(tmp_path, "api/routes/sessions.py", "from ...daytona import provider\n")

    boundary_violations, clarity_violations = check_codebase_tree(tmp_path)
    assert clarity_violations == []
    assert any("api/routes/sessions.py:1" in item for item in boundary_violations)
    assert any("route bypasses injected application modules" in item for item in boundary_violations)


def test_relative_persistence_import_is_reported_in_routes(tmp_path: Path) -> None:
    _write_src(tmp_path, "persistence/models.py", "from dataclasses import dataclass\n")
    _write_src(
        tmp_path,
        "api/routes/turns.py",
        "from ...persistence.repositories.turns import SqlAlchemyRunStateStore\n",
    )

    boundary_violations, _ = check_codebase_tree(tmp_path)
    assert any("api/routes/turns.py:1" in item for item in boundary_violations)
    assert any("route bypasses injected application modules" in item for item in boundary_violations)


def test_small_file_outside_exempt_lanes_is_reported(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/optimization/test_tiny.py", "def test_only() -> None: ...\n")

    violations = check_test_layout(tmp_path)
    assert any("test_tiny.py" in item and "1 case(s)" in item for item in violations)


@pytest.mark.parametrize("lane", ["tree", "dependencies", "all"])
def test_architecture_cli_dispatches_each_check_lane(lane: str) -> None:
    assert main([lane]) == 0


# --- Dependency Boundary Tests ---


def test_checker_accepts_the_narrow_storage_transport_exception(tmp_path: Path) -> None:
    _write_src(
        tmp_path,
        "workspace/storage.py",
        "from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent\n",
    )
    _write_src(tmp_path, "workspace/models.py", "from dataclasses import dataclass\n")
    _write_src(tmp_path, "daytona/client.py", "from collections.abc import Mapping\n")
    _write_src(tmp_path, "persistence/repositories/outbox.py", "from dataclasses import dataclass\n")
    _write_src(tmp_path, "rlm/program.py", "from dspy import Signature\n")

    assert check_dependency_boundaries(tmp_path) == ()


def test_checker_reports_local_imports_and_new_scope_edges(tmp_path: Path) -> None:
    _write_src(tmp_path, "workspace/workspace.py", "from fleet_rlm.chat import turn_runtime\n")
    _write_src(tmp_path, "daytona/provider.py", "def prepare():\n    from fleet_rlm.chat import preparation\n")
    _write_src(tmp_path, "persistence/repositories/outbox.py", "from fleet_rlm.rlm.result import Result\n")
    _write_src(tmp_path, "rlm/runtime.py", "import fastapi\n")
    _write_src(tmp_path, "chat/preparation.py", "import fastapi\n")
    _write_src(tmp_path, "persistence/repositories/turns.py", "from fleet_rlm.api import dependencies\n")
    _write_src(tmp_path, "sessions/models.py", "from fleet_rlm.persistence import database\n")
    _write_src(tmp_path, "skills/tools.py", "from fleet_rlm.daytona import broker\n")

    violations = check_dependency_boundaries(tmp_path)
    rendered = "\n".join(item.render() for item in violations)

    assert "workspace/workspace.py:1" in rendered
    assert "workspace must not import chat" in rendered
    assert "daytona/provider.py:2" in rendered
    assert "daytona must not import chat" in rendered
    assert "persistence/repositories/outbox.py:1" in rendered
    assert "persistence must not import rlm" in rendered
    assert "rlm/runtime.py:1" in rendered
    assert "rlm must not import FastAPI" in rendered
    assert "chat/preparation.py:1" in rendered
    assert "chat must not import FastAPI" in rendered
    assert "persistence/repositories/turns.py:1" in rendered
    assert "persistence must not import api" in rendered
    assert "sessions/models.py:1" in rendered
    assert "sessions must not import persistence" in rendered
    assert "skills/tools.py:1" in rendered
    assert "skills must not import Daytona provider modules" in rendered


def test_chat_cycle_exceptions_are_shrink_only(tmp_path: Path) -> None:
    _write_src(tmp_path, "rlm/runtime.py", "from fleet_rlm.sessions.context import SessionContextManifest\n")
    _write_src(tmp_path, "rlm/events.py", "from fleet_rlm.turns.preparation import RunPreparation\n")
    _write_src(
        tmp_path,
        "persistence/repositories/turns.py",
        "from fleet_rlm.sessions.run_claim import decide_claim_transition\n",
    )
    _write_src(
        tmp_path,
        "persistence/repositories/outbox.py",
        "from fleet_rlm.turns.settlement import RunSettlementPlan\n",
    )
    _write_src(
        tmp_path,
        "sessions/catalog.py",
        "from fleet_rlm.turns.settlement import RunSettlementPlan\n",
    )

    violations = check_dependency_boundaries(tmp_path)
    rendered = "\n".join(item.render() for item in violations)

    assert "rlm/runtime.py:1" not in rendered
    assert "persistence/repositories/turns.py:1" not in rendered
    assert "rlm/events.py:1" in rendered
    assert "rlm must not import turn coordination" in rendered
    assert "persistence/repositories/outbox.py:1" in rendered
    assert "persistence must not import turn coordination" in rendered
    assert "sessions/catalog.py:1" in rendered
    assert "sessions must not import turn coordination" in rendered
