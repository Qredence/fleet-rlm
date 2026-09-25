"""Exercise maintenance targets with disposable files and fake external tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _workspace(path: Path) -> None:
    shutil.copyfile(REPO_ROOT / "Makefile", path / "Makefile")
    (path / "tools/fleet-tui/src").mkdir(parents=True)


def test_clean_preserves_local_data_and_dependencies(tmp_path: Path) -> None:
    _workspace(tmp_path)
    preserved = [
        "fleet_rlm.db",
        "server.log",
        ".env",
        ".venv/lib/__pycache__/module.pyc",
        "tools/fleet-tui/node_modules/pkg/__pycache__/module.pyc",
        "src/nested/.venv/__pycache__/module.pyc",
        ".scratch/evidence.json",
        "src/module.py",
    ]
    removed = [
        "src/pkg/__pycache__/module.pyc",
        ".ruff_cache/cache",
        ".ty/cache",
        "build/package",
        "dist/package.whl",
        "src/fleet_rlm.egg-info/PKG-INFO",
        ".coverage",
        ".scratch/coverage/daytona.xml",
    ]
    for name in preserved + removed:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("sentinel")

    subprocess.run(["make", "clean"], cwd=tmp_path, check=True, capture_output=True)

    assert all((tmp_path / name).read_text() == "sentinel" for name in preserved)
    assert all(not (tmp_path / name).exists() for name in removed)


def _run_fake_tools(
    path: Path, *targets: str, fail_lint: bool = False
) -> tuple[subprocess.CompletedProcess[str], list]:
    _workspace(path)
    bindir = path / "bin"
    bindir.mkdir()
    script = (
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["MAKE_TEST_LOG"])
with log.open("a") as output:
    output.write(json.dumps(args) + "\\n")
if os.environ.get("MAKE_TEST_FAIL") and args[:3] == ["run", "ruff", "check"]:
    sys.exit(7)
if args == ["build"]:
    previous = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["run", "python", "scripts/validate_release.py", "metadata"] in previous
    assert ["pip-audit"] in previous
    assert any(command[:3] == ["run", "--no-sync", "pytest"] for command in previous)
    assert not Path("dist/old.whl").exists()
"""
    )
    for name in ("uv", "uvx", "pnpm"):
        tool = bindir / name
        tool.write_text(script)
        tool.chmod(0o755)
    (path / "dist").mkdir()
    (path / "dist/old.whl").write_text("old")
    log = path / "commands.jsonl"
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "MAKE_TEST_LOG": str(log)}
    if fail_lint:
        env["MAKE_TEST_FAIL"] = "1"
    result = subprocess.run(["make", "-j4", *targets], cwd=path, env=env, capture_output=True, text=True)
    return result, [json.loads(line) for line in log.read_text().splitlines()]


def test_quality_graph_runs_shared_checks_once(tmp_path: Path) -> None:
    result, commands = _run_fake_tools(tmp_path, "check", "check-release")
    assert result.returncode == 0, result.stderr
    for command in (
        ["run", "python", "scripts/openapi_tools.py", "check"],
        ["run", "python", "scripts/generate_stream_fixture.py", "check"],
        ["run", "python", "scripts/check_agents_md_freshness.py"],
    ):
        assert commands.count(command) == 1


def test_install_targets_select_dependency_groups(tmp_path: Path) -> None:
    result, commands = _run_fake_tools(tmp_path, "install", "install-dev", "install-all")
    assert result.returncode == 0, result.stderr
    assert ["sync", "--no-dev"] in commands
    assert ["sync", "--dev"] in commands
    assert ["sync", "--all-extras", "--dev"] in commands


def test_coverage_target_and_legacy_alias_share_the_project_suite(tmp_path: Path) -> None:
    result, commands = _run_fake_tools(tmp_path, "test-coverage", "test-daytona-cov")
    assert result.returncode == 0, result.stderr
    pytest_commands = [command for command in commands if command[:3] == ["run", "--no-sync", "pytest"]]
    assert len(pytest_commands) == 1
    assert "--cov" in pytest_commands[0]
    assert "--cov-report=xml:.scratch/coverage/daytona.xml" in pytest_commands[0]


def test_security_audit_arguments_are_overridable(tmp_path: Path) -> None:
    result, commands = _run_fake_tools(
        tmp_path,
        "check-security",
        "PIP_AUDIT_ARGS=--ignore-vuln GHSA-example",
    )
    assert result.returncode == 0, result.stderr
    assert ["pip-audit", "--ignore-vuln", "GHSA-example"] in commands
    assert ["bandit", "-q", "-r", "src/fleet_rlm", "-x", "tests", "-lll"] in commands


@pytest.mark.parametrize("fail_lint", [False, True])
def test_release_build_waits_for_successful_checks(tmp_path: Path, fail_lint: bool) -> None:
    result, commands = _run_fake_tools(tmp_path, "release", fail_lint=fail_lint)
    if fail_lint:
        assert result.returncode != 0
        assert ["build"] not in commands
        assert (tmp_path / "dist/old.whl").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert commands.count(["build"]) == 1
