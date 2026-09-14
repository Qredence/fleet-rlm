from __future__ import annotations

import ast
from pathlib import Path

from scripts.import_walk import iter_imports, matches, resolve_from_import


def test_matches_exact_and_prefix() -> None:
    assert matches("fleet_rlm.chat", "fleet_rlm.chat")
    assert matches("fleet_rlm.chat.session", "fleet_rlm.chat")
    assert not matches("fleet_rlm.chatter", "fleet_rlm.chat")


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


def test_resolve_from_import_relative_levels() -> None:
    source_root = Path("/repo/src")
    path = source_root / "fleet_rlm" / "api" / "routes" / "sessions.py"
    node = ast.parse("from ...daytona import provider").body[0]
    assert isinstance(node, ast.ImportFrom)

    resolved = list(resolve_from_import(node, path=path, source_root=source_root))

    assert resolved == ["fleet_rlm.daytona", "fleet_rlm.daytona.provider"]


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
