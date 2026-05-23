from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / ".venv" / "bin"


@pytest.mark.parametrize(
    ("command", "expected_terms"),
    [
        ("fleet", ("web", "--trace-mode")),
        ("fleet-rlm", ("chat", "serve-api", "daytona-smoke")),
    ],
)
def test_cli_help_smoke(command: str, expected_terms: tuple[str, ...]) -> None:
    cli_path = BIN_DIR / command
    if not cli_path.exists():
        resolved = shutil.which(command)
        assert resolved is not None, f"{command} is not installed"
        cli_path = Path(resolved)

    env = {**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "true"}

    result = subprocess.run(
        [str(cli_path), "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    output = f"{result.stdout}\n{result.stderr}"
    for expected in expected_terms:
        assert expected in output
