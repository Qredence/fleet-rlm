#!/usr/bin/env python3
"""Checks release metadata consistency across versioned files."""

from __future__ import annotations

import pathlib
import re
import sys

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INIT_PATH = REPO_ROOT / "src" / "fleet_rlm" / "__init__.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
OPENAPI_PATH = REPO_ROOT / "openapi.yaml"
FRONTEND_OPENAPI_SNAPSHOT_PATH = (
    REPO_ROOT / "src" / "frontend" / "openapi" / "fleet-rlm.openapi.yaml"
)

INIT_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def read_pyproject_version() -> str:
    with PYPROJECT_PATH.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def read_init_version() -> str:
    text = INIT_PATH.read_text(encoding="utf-8")
    match = INIT_VERSION_PATTERN.search(text)
    if not match:
        raise ValueError("Could not locate __version__ in src/fleet_rlm/__init__.py")
    return match.group(1)


def changelog_has_version(version: str) -> bool:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    patterns = (
        f"## {version} - ",
        f"## [{version}] - ",
    )
    return any(pattern in text for pattern in patterns)


def check_openapi_layout() -> tuple[bool, str]:
    if not OPENAPI_PATH.exists():
        return False, "ERROR: Missing canonical OpenAPI spec at openapi.yaml"

    if (
        REPO_ROOT / "src" / "frontend"
    ).exists() and not FRONTEND_OPENAPI_SNAPSHOT_PATH.exists():
        return (
            False,
            "ERROR: Missing frontend OpenAPI snapshot at "
            "src/frontend/openapi/fleet-rlm.openapi.yaml",
        )

    return True, "OK: OpenAPI files are present in expected locations."


def main() -> int:
    pyproject_version = read_pyproject_version()
    init_version = read_init_version()

    if pyproject_version != init_version:
        print(
            "ERROR: Version mismatch: "
            f"pyproject.toml={pyproject_version} vs __init__.py={init_version}"
        )
        return 1

    if not changelog_has_version(pyproject_version):
        print(f"ERROR: CHANGELOG.md is missing release header for {pyproject_version}")
        return 1

    openapi_ok, openapi_message = check_openapi_layout()
    print(openapi_message)
    if not openapi_ok:
        return 1

    print(
        "OK: Release metadata is consistent "
        f"(version={pyproject_version}, changelog header present)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
