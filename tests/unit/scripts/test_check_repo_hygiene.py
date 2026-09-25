from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import check_repo_hygiene as hygiene


def test_consolidated_entrypoint_runs_each_ci_checker_once(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class Agents:
        def __init__(self, _root: Path) -> None:
            calls.append("agents")

        def validate_all(self):
            return [SimpleNamespace(file="AGENTS.md", issue="broken", detail="sentinel")]

    class Harness:
        def __init__(self, _root: Path, **_kwargs) -> None:
            calls.append("harness")

        def run(self):
            return [SimpleNamespace(path="scripts/README.md", detail="missing inventory")]

    monkeypatch.setattr(hygiene, "AgentsMdValidator", Agents)
    monkeypatch.setattr(hygiene, "run_docs_checks", lambda _root: calls.append("docs") or ["broken link"])
    monkeypatch.setattr(hygiene, "HarnessChecker", Harness)
    monkeypatch.setattr(hygiene, "check_active_script_references", lambda _root: [])
    monkeypatch.setattr(hygiene, "check_retired_commands_absent", lambda _root: [])

    errors = hygiene.run_checks(tmp_path, check_script_help=False)

    assert calls == ["agents", "docs", "harness"]
    assert len(errors) == 3
    assert any("AGENTS [AGENTS.md]" in error for error in errors)
    assert any("docs broken link" in error for error in errors)
    assert any("harness [scripts/README.md]" in error for error in errors)


def test_active_script_references_must_resolve(tmp_path: Path) -> None:
    guide = tmp_path / "docs/how-to-guides/scripts.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("Use `scripts/current.py` and `scripts/removed.py`.\n", encoding="utf-8")
    current = tmp_path / "scripts/current.py"
    current.parent.mkdir()
    current.write_text("# helper\n", encoding="utf-8")

    assert hygiene.check_active_script_references(tmp_path) == [
        "docs/how-to-guides/scripts.md: references missing script scripts/removed.py"
    ]


def test_retired_command_is_rejected_from_active_guides(tmp_path: Path) -> None:
    guide = tmp_path / "docs/how-to-guides/old.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("Run scripts/live_phase5_verify.py\n", encoding="utf-8")

    errors = hygiene.check_retired_commands_absent(tmp_path)

    assert errors == ["docs/how-to-guides/old.md: retired command remains active: scripts/live_phase5_verify.py"]


def test_help_is_inert() -> None:
    script = Path(hygiene.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--repo-root" in result.stdout
    assert "--editorial" in result.stdout
