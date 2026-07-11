"""Parallel clean-backend package bootstrap (K-001).

This package is intentionally separate from ``fleet_rlm`` until cutover.
Importing it must not open network connections or construct external clients.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

_PYPROJECT_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _load_version_from_pyproject() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    match = _PYPROJECT_VERSION_PATTERN.search(pyproject_path.read_text(encoding="utf-8"))
    if match is None:
        msg = "Could not locate [project].version in pyproject.toml"
        raise RuntimeError(msg)
    return match.group(1)


def _resolve_version() -> str:
    try:
        return package_version("fleet-rlm")
    except PackageNotFoundError:
        return _load_version_from_pyproject()


__version__ = _resolve_version()

__all__ = ["__version__"]
