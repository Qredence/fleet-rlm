from __future__ import annotations

import ast
from pathlib import Path

from scripts.check_codebase_tree import check_codebase_tree, find_nested_ternaries


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
