from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import check_repo_hygiene as hygiene


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


def test_active_reference_scan_includes_workflows_bootstrap_and_skills(tmp_path: Path) -> None:
    files = {
        ".github/workflows/ci.yml": "run: uv run python scripts/from-workflow.py\n",
        ".codex/cloud-preflight.zsh": "uv run python scripts/from-bootstrap.py\n",
        ".agents/skills/example/SKILL.md": "Follow the Fleet script guidance.\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    errors = hygiene.check_active_script_references(tmp_path)

    assert len(errors) == 2
    assert any(".github/workflows/ci.yml" in error for error in errors)
    assert any(".codex/cloud-preflight.zsh" in error for error in errors)


def test_retired_commands_are_rejected_from_skills(tmp_path: Path) -> None:
    skill = tmp_path / ".agents/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Do not run `scripts/live_phase5_verify.py`.\n", encoding="utf-8")

    assert hygiene.check_retired_commands_absent(tmp_path) == [
        ".agents/skills/example/SKILL.md: retired command remains active: scripts/live_phase5_verify.py"
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
