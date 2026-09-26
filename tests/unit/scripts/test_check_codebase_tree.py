from __future__ import annotations

import ast
from pathlib import Path

from scripts.check_codebase_tree import check_codebase_tree, check_test_layout, find_nested_ternaries


def test_nested_ifexp_is_reported() -> None:
    tree = ast.parse("value = a if cond else (b if other else c)\n")

    assert find_nested_ternaries(tree) == [1]


def test_simple_ifexp_is_allowed() -> None:
    tree = ast.parse("value = a if cond else b\n")

    assert find_nested_ternaries(tree) == []


def test_nested_ifexp_in_body_is_reported() -> None:
    tree = ast.parse("value = (a if inner else b) if outer else c\n")

    assert find_nested_ternaries(tree) == [1]


def test_ifexp_inside_call_in_branch_is_reported() -> None:
    tree = ast.parse("value = foo(a if x else b) if cond else default\n")

    assert find_nested_ternaries(tree) == [1]


def test_ifexp_only_in_condition_is_allowed() -> None:
    tree = ast.parse("value = a if (b if c else d) else e\n")

    assert find_nested_ternaries(tree) == []


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


def test_flat_backend_unit_root_file_is_reported(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/unit/backend/test_flat.py", "def test_a() -> None: ...\n" * 3)

    violations = check_test_layout(tmp_path)

    assert any("flat file in the backend unit root" in item for item in violations)


def test_small_file_outside_exempt_lanes_is_reported(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/unit/optimization/test_tiny.py", "def test_only() -> None: ...\n")

    violations = check_test_layout(tmp_path)

    assert any("test_tiny.py" in item and "1 case(s)" in item for item in violations)


def test_small_file_in_exempt_lane_is_allowed(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/live/backend/test_canary.py", "def test_only() -> None: ...\n")
    _write_test(tmp_path, "tests/contracts/backend/test_api.py", "def test_only() -> None: ...\n")

    assert check_test_layout(tmp_path) == []


def test_organized_backend_file_is_allowed(tmp_path: Path) -> None:
    _write_test(tmp_path, "tests/unit/backend/turn/test_owner.py", "def test_a() -> None: ...\n" * 3)

    assert check_test_layout(tmp_path) == []


def test_missing_tests_directory_is_allowed(tmp_path: Path) -> None:
    assert check_test_layout(tmp_path) == []
