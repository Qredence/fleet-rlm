from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_import_dspy_avoids_avatar_prefix_deprecation_warnings() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    completed = subprocess.run(
        [sys.executable, "-W", "default", "-c", "import dspy"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "The 'prefix' argument in InputField/OutputField is deprecated" not in completed.stderr
