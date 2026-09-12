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
    removed = ["src/pkg/__pycache__/module.pyc", ".ruff_cache/cache", "build/package", "dist/package.whl", ".coverage"]
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
    assert ["run", "test"] in previous
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


@pytest.mark.parametrize("fail_lint", [False, True])
def test_release_build_waits_for_successful_checks(tmp_path: Path, fail_lint: bool) -> None:
    result, commands = _run_fake_tools(tmp_path, "release", fail_lint=fail_lint)
    if fail_lint:
        assert result.returncode != 0
        assert ["build"] not in commands
    else:
        assert result.returncode == 0, result.stderr
        assert commands.count(["build"]) == 1
