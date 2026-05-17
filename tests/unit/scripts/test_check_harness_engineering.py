"""Tests for harness engineering validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.check_harness_engineering import HarnessChecker


def write_valid_repo(root: Path) -> None:
    """Create the smallest repo shape accepted by the harness checker."""
    (root / ".codex" / "agents").mkdir(parents=True)
    (root / ".codex" / "environments").mkdir(parents=True)
    (root / ".codex" / "hooks").mkdir(parents=True)
    (root / "docs" / "agent-harness").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "src" / "fleet_rlm" / "integrations" / "config").mkdir(parents=True)
    (root / "src" / "frontend" / "src" / "components" / "ui").mkdir(parents=True)

    (root / "AGENTS.md").write_text("# Root\n\nSee docs/agent-harness/README.md\n", encoding="utf-8")
    for index in ("README.md", "index.md", "SUMMARY.md"):
        (root / "docs" / index).write_text("[Harness](agent-harness/README.md)\n", encoding="utf-8")

    docs_text = "\n".join(
        [
            "openapi.yaml",
            "src/frontend/src/lib/rlm-api/generated/openapi.ts",
            "src/frontend/openapi/fleet-rlm.openapi.yaml",
            "src/frontend/src/routeTree.gen.ts",
            "src/frontend/dist",
            "src/fleet_rlm/ui/dist",
            "make api-sync",
            "make api-check",
            "make build-ui",
        ]
    )
    for name in ("README.md", "feedback-loop.md", "architecture-invariants.md", "quality-score.md", "drift-control.md"):
        (root / "docs" / "agent-harness" / name).write_text(docs_text, encoding="utf-8")

    (root / ".codex" / "config.toml").write_text("[features]\nhooks = true\n", encoding="utf-8")
    (root / ".codex" / "environments" / "environment.toml").write_text("version = 1\n", encoding="utf-8")
    (root / ".codex" / "agents" / "backend.toml").write_text("name = 'backend'\n", encoding="utf-8")
    (root / ".codex" / "hooks.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    for name in ("workspace-bootstrap.zsh",):
        (root / ".codex" / name).write_text("#!/usr/bin/env zsh\n", encoding="utf-8")
    for name in ("block-env-edit.zsh", "generated-artifact-check.zsh", "python-format.zsh"):
        (root / ".codex" / "hooks" / name).write_text("#!/usr/bin/env zsh\n", encoding="utf-8")

    helper = root / "scripts" / "tool.py"
    helper.write_text(
        "import argparse\nargparse.ArgumentParser().parse_args()\n",
        encoding="utf-8",
    )
    (root / "scripts" / "README.md").write_text("`tool.py`\n", encoding="utf-8")
    (root / "src" / "fleet_rlm" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "fleet_rlm" / "integrations" / "config" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "fleet_rlm" / "integrations" / "config" / "settings.py").write_text("", encoding="utf-8")


def run_checker(root: Path, *, check_script_help: bool = True) -> list[str]:
    """Return formatted checker failures."""
    errors = HarnessChecker(repo_root=root, check_script_help=check_script_help).run()
    return [f"{error.path}: {error.detail}" for error in errors]


def test_valid_minimal_repo_passes(tmp_path: Path) -> None:
    """A minimal valid repo has no harness errors."""
    write_valid_repo(tmp_path)

    assert run_checker(tmp_path) == []


def test_root_agents_line_budget_fails(tmp_path: Path) -> None:
    """Root AGENTS.md must stay within the line budget."""
    write_valid_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("\n".join(["line"] * 141), encoding="utf-8")

    errors = run_checker(tmp_path, check_script_help=False)

    assert any("root guide has 141 lines" in error for error in errors)


def test_malformed_codex_config_fails(tmp_path: Path) -> None:
    """Malformed TOML is reported as a harness error."""
    write_valid_repo(tmp_path)
    (tmp_path / ".codex" / "config.toml").write_text("[broken\n", encoding="utf-8")

    errors = run_checker(tmp_path, check_script_help=False)

    assert any(".codex/config.toml" in error and "TOML parse failed" in error for error in errors)


def test_script_inventory_fails_for_unlisted_helper(tmp_path: Path) -> None:
    """Top-level Python helpers must be listed in scripts/README.md."""
    write_valid_repo(tmp_path)
    (tmp_path / "scripts" / "README.md").write_text("", encoding="utf-8")

    errors = run_checker(tmp_path, check_script_help=False)

    assert any("scripts/tool.py" in error and "missing from scripts/README.md" in error for error in errors)


def test_script_help_failure_is_reported(tmp_path: Path) -> None:
    """Retained Python helpers must support --help without side effects."""
    write_valid_repo(tmp_path)
    (tmp_path / "scripts" / "tool.py").write_text(f"import sys\nsys.exit(3)\n# {sys.executable}\n", encoding="utf-8")

    errors = run_checker(tmp_path)

    assert any("scripts/tool.py" in error and "--help failed" in error for error in errors)
