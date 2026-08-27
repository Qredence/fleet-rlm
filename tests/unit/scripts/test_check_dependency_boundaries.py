"""Unit tests for the P50 dependency-direction migration gate."""

from __future__ import annotations

from pathlib import Path

from scripts.check_dependency_boundaries import check_dependency_boundaries, main


def _write(root: Path, relative: str, source: str) -> None:
    path = root / "src" / "fleet_rlm" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_checker_accepts_the_narrow_storage_transport_exception(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "workspace/storage.py",
        "from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent\n",
    )
    _write(tmp_path, "workspace/models.py", "from dataclasses import dataclass\n")
    _write(tmp_path, "daytona/client.py", "from collections.abc import Mapping\n")
    _write(tmp_path, "persistence/repositories/outbox.py", "from dataclasses import dataclass\n")
    _write(tmp_path, "rlm/program.py", "from dspy import Signature\n")

    assert check_dependency_boundaries(tmp_path) == ()


def test_checker_reports_local_imports_and_legacy_files_edges(tmp_path: Path) -> None:
    _write(tmp_path, "workspace/workspace.py", "from fleet_rlm.files import legacy\n")
    _write(tmp_path, "daytona/run_environment.py", "def prepare():\n    from fleet_rlm.chat import preparation\n")
    _write(tmp_path, "persistence/repositories/outbox.py", "from fleet_rlm.rlm.result import Result\n")
    _write(tmp_path, "rlm/runtime.py", "import fastapi\n")

    violations = check_dependency_boundaries(tmp_path)
    rendered = "\n".join(item.render() for item in violations)

    assert "workspace/workspace.py:1" in rendered
    assert "workspace must not import the legacy files package" in rendered
    assert "daytona/run_environment.py:2" in rendered
    assert "daytona must not import chat" in rendered
    assert "persistence/repositories/outbox.py:1" in rendered
    assert "persistence must not import rlm" in rendered
    assert "rlm/runtime.py:1" in rendered
    assert "rlm must not import FastAPI" in rendered


def test_checker_reports_daytona_memory_content_even_without_imports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "daytona/transport.py",
        'def execute():\n    return {"operation": "memory_append"}\n',
    )

    violations = check_dependency_boundaries(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == "src/fleet_rlm/daytona/transport.py"
    assert violations[0].line == 2
    assert "Memory domain policy" in violations[0].rule


def test_main_reports_current_legacy_edges_with_nonzero_status(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "daytona/run_environment.py", "from fleet_rlm.chat import preparation\n")

    assert main(["--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "Dependency boundary check failed" in captured.err
    assert "daytona/run_environment.py:1" in captured.err


def test_main_accepts_a_clean_destination_tree(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "workspace/storage.py", "from fleet_rlm.daytona.workspace_agent.client import execute\n")
    _write(tmp_path, "daytona/transport.py", "def execute():\n    return None\n")

    assert main(["--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "Dependency boundary check passed\n"
