"""Installed-package startup hook for Fleet DSPy compatibility patches."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_patch_installer() -> ModuleType | None:
    install_path = Path(__file__).resolve().parent / "fleet_rlm" / "_vendor" / "dspy_patches" / "install.py"
    spec = importlib.util.spec_from_file_location("_fleet_rlm_dspy_patch_install", install_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_installer = _load_patch_installer()
if _installer is not None:
    _installer.install()
