#!/usr/bin/env python3
"""Checks release metadata consistency across versioned files."""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

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
FRONTEND_OPENAPI_TYPES_PATH = (
    REPO_ROOT
    / "src"
    / "frontend"
    / "src"
    / "lib"
    / "rlm-api"
    / "generated"
    / "openapi.ts"
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


def _load_openapi_document(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)

    if not isinstance(loaded, dict):
        raise ValueError(f"OpenAPI document at {path} is not a YAML mapping")
    return loaded


def _extract_openapi_version(document: dict, *, source_name: str) -> str:
    info = document.get("info")
    if not isinstance(info, dict):
        raise ValueError(f"OpenAPI {source_name} is missing 'info' mapping")

    version = info.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"OpenAPI {source_name} is missing a non-empty info.version")

    return version.strip()


def check_openapi_contract(pyproject_version: str) -> tuple[bool, str]:
    if not OPENAPI_PATH.exists():
        return False, "ERROR: Missing canonical OpenAPI spec at openapi.yaml"

    frontend_root = REPO_ROOT / "src" / "frontend"
    if frontend_root.exists() and not FRONTEND_OPENAPI_SNAPSHOT_PATH.exists():
        return (
            False,
            "ERROR: Missing frontend OpenAPI snapshot at "
            "src/frontend/openapi/fleet-rlm.openapi.yaml",
        )

    try:
        root_openapi = _load_openapi_document(OPENAPI_PATH)
        root_version = _extract_openapi_version(
            root_openapi, source_name="root openapi.yaml"
        )
    except ValueError as exc:
        return False, f"ERROR: {exc}"

    if root_version != pyproject_version:
        return (
            False,
            "ERROR: OpenAPI version mismatch: "
            f"openapi.yaml={root_version} vs pyproject.toml={pyproject_version}",
        )

    if not frontend_root.exists():
        return True, "OK: OpenAPI root contract matches release version."

    if not FRONTEND_OPENAPI_TYPES_PATH.exists():
        return (
            False,
            "ERROR: Missing generated frontend OpenAPI types at "
            "src/frontend/src/lib/rlm-api/generated/openapi.ts",
        )

    try:
        frontend_openapi = _load_openapi_document(FRONTEND_OPENAPI_SNAPSHOT_PATH)
        frontend_version = _extract_openapi_version(
            frontend_openapi, source_name="frontend OpenAPI snapshot"
        )
    except ValueError as exc:
        return False, f"ERROR: {exc}"

    if frontend_version != pyproject_version:
        return (
            False,
            "ERROR: Frontend OpenAPI version mismatch: "
            "src/frontend/openapi/fleet-rlm.openapi.yaml="
            f"{frontend_version} vs pyproject.toml={pyproject_version}",
        )

    if root_openapi != frontend_openapi:
        return (
            False,
            "ERROR: OpenAPI drift detected: root openapi.yaml and frontend snapshot "
            "src/frontend/openapi/fleet-rlm.openapi.yaml differ",
        )

    return True, "OK: OpenAPI contracts are version-aligned and in sync."


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

    openapi_ok, openapi_message = check_openapi_contract(pyproject_version)
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
