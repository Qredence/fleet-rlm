from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


_REPO_ROOT = Path(__file__).resolve().parent
_FRONTEND_DIR = _REPO_ROOT / "src" / "frontend"
_BUILD_UI_SCRIPT = _REPO_ROOT / "scripts" / "build_ui.py"
_BUILD_SENTINEL_ENV = "FLEET_RLM_UI_BUILD_DONE"


def _maybe_build_frontend_ui() -> None:
    """Build/sync frontend assets before packaging when source frontend is present."""
    if os.environ.get(_BUILD_SENTINEL_ENV) == "1":
        return

    if not _FRONTEND_DIR.exists():
        # Downstream sdist builds may not include the frontend source tree.
        print("Skipping frontend UI build: src/frontend not present.")
        return

    if not _BUILD_UI_SCRIPT.exists():
        if (_REPO_ROOT / ".git").exists():
            raise RuntimeError(
                f"Missing frontend build helper script: {_BUILD_UI_SCRIPT}"
            )
        print("Skipping frontend UI build: scripts/build_ui.py not present.")
        return

    print("Running frontend packaging build via scripts/build_ui.py ...")
    subprocess.run([sys.executable, str(_BUILD_UI_SCRIPT)], cwd=_REPO_ROOT, check=True)
    os.environ[_BUILD_SENTINEL_ENV] = "1"


class build_py(_build_py):
    def run(self) -> None:
        _maybe_build_frontend_ui()
        super().run()


class sdist(_sdist):
    def run(self) -> None:
        _maybe_build_frontend_ui()
        super().run()


setup(cmdclass={"build_py": build_py, "sdist": sdist})
