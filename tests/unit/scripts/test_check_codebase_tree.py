from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_architecture import (
    main,
)
from scripts.check_architecture import (
    tree_check_codebase_tree as check_codebase_tree,
)
from scripts.check_architecture import (
    tree_check_test_layout as check_test_layout,
)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / "src" / "fleet_rlm" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_relative_daytona_route_import_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "daytona/provider.py", "def provider() -> None: ...\n")
    _write(tmp_path, "api/routes/sessions.py", "from ...daytona import provider\n")

    boundary_violations, clarity_violations = check_codebase_tree(tmp_path)

    assert clarity_violations == []
    assert any("api/routes/sessions.py:1" in item for item in boundary_violations)
    assert any("route bypasses injected application modules" in item for item in boundary_violations)


def test_relative_persistence_import_is_reported_in_routes(tmp_path: Path) -> None:
    _write(tmp_path, "persistence/models.py", "from dataclasses import dataclass\n")
    _write(tmp_path, "api/routes/turns.py", "from ...persistence.repositories.turns import SqlAlchemyRunStateStore\n")

    boundary_violations, _ = check_codebase_tree(tmp_path)

    assert any("api/routes/turns.py:1" in item for item in boundary_violations)
    assert any("route bypasses injected application modules" in item for item in boundary_violations)


def _write_test(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_small_file_outside_exempt_lanes_is_reported(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/unit/optimization/test_tiny.py", "def test_only() -> None: ...\n")

    violations = check_test_layout(tmp_path)

    assert any("test_tiny.py" in item and "1 case(s)" in item for item in violations)


@pytest.mark.parametrize("lane", ["tree", "dependencies", "all"])
def test_architecture_cli_dispatches_each_check_lane(lane: str) -> None:
    assert main([lane]) == 0
