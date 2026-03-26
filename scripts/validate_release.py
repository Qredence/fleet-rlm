#!/usr/bin/env python3
"""Unified CLI for release validation tasks."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INIT_PATH = REPO_ROOT / "src" / "fleet_rlm" / "__init__.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
OPENAPI_PATH = REPO_ROOT / "openapi.yaml"
FRONTEND_ROOT = REPO_ROOT / "src" / "frontend"
FRONTEND_OPENAPI_SNAPSHOT_PATH = FRONTEND_ROOT / "openapi" / "fleet-rlm.openapi.yaml"
FRONTEND_OPENAPI_TYPES_PATH = (
    FRONTEND_ROOT / "src" / "lib" / "rlm-api" / "generated" / "openapi.ts"
)

ENV_EXAMPLE_SUFFIXES = (".env.example",)
ENV_ALLOWED_EXACT = {".env.example", "src/frontend/.env.example"}
FORBIDDEN_TRACKED_ENV_PATTERN = re.compile(r"(^|/)\.env(\..+)?$")
FORBIDDEN_TRACKED_TMP_PATTERNS = (
    re.compile(r"\.tmp$", re.IGNORECASE),
    re.compile(r"(^|/)\.tmp($|[/_-])"),
    re.compile(r"(^|/)tmp($|/)"),
)
FORBIDDEN_TRACKED_MJS_PATTERN = re.compile(r"\.mjs$")
ALLOWED_TRACKED_MJS_EXACT = {
    "src/frontend/postcss.config.mjs",
    "src/frontend/scripts/check-api-sync.mjs",
}
INIT_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
WHEEL_UI_PREFIX = "fleet_rlm/ui/dist/"


def _git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    return [path for path in out.split("\0") if path]


def _is_allowed_env_example(path: str) -> bool:
    if path in ENV_ALLOWED_EXACT:
        return True
    return path.endswith(ENV_EXAMPLE_SUFFIXES)


def _is_forbidden_tmp_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_TMP_PATTERNS)


def _read_pyproject_version() -> str:
    with PYPROJECT_PATH.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def _read_init_version() -> str:
    text = INIT_PATH.read_text(encoding="utf-8")
    match = INIT_VERSION_PATTERN.search(text)
    if not match:
        raise ValueError("Could not locate __version__ in src/fleet_rlm/__init__.py")
    return match.group(1)


def _changelog_has_version(version: str) -> bool:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    patterns = (f"## {version} - ", f"## [{version}] - ")
    return any(pattern in text for pattern in patterns)


def _load_openapi_document(path: Path) -> dict:
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


def _check_openapi_contract(pyproject_version: str) -> tuple[bool, str]:
    if not OPENAPI_PATH.exists():
        return False, "ERROR: Missing canonical OpenAPI spec at openapi.yaml"

    if FRONTEND_ROOT.exists() and not FRONTEND_OPENAPI_SNAPSHOT_PATH.exists():
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

    if not FRONTEND_ROOT.exists():
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _latest_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("fleet_rlm-*.whl"))
    if not wheels:
        raise FileNotFoundError(f"No wheel found in {dist_dir}")
    return wheels[-1]


def _collect_local_frontend(frontend_dist: Path) -> dict[str, str]:
    if not frontend_dist.exists():
        raise FileNotFoundError(f"Frontend dist not found at {frontend_dist}")
    return {
        str(path.relative_to(frontend_dist)): _sha256_file(path)
        for path in sorted(frontend_dist.rglob("*"))
        if path.is_file()
    }


def _collect_wheel_frontend(wheel_path: Path) -> tuple[dict[str, str], list[str]]:
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        stray_frontend = [
            name
            for name in names
            if name.startswith("frontend/") and not name.endswith("/")
        ]
        ui_hashes: dict[str, str] = {}
        for name in names:
            if not name.startswith(WHEEL_UI_PREFIX) or name.endswith("/"):
                continue
            relative_name = name[len(WHEEL_UI_PREFIX) :]
            ui_hashes[relative_name] = _sha256_bytes(wheel.read(name))
        return ui_hashes, stray_frontend


def do_hygiene(args: argparse.Namespace) -> int:
    _ = args
    tracked = _git_ls_files()
    forbidden_env = [
        path
        for path in tracked
        if FORBIDDEN_TRACKED_ENV_PATTERN.search(path)
        and not _is_allowed_env_example(path)
    ]
    forbidden_tmp = [path for path in tracked if _is_forbidden_tmp_path(path)]
    forbidden_mjs = [
        path
        for path in tracked
        if FORBIDDEN_TRACKED_MJS_PATTERN.search(path)
        and path not in ALLOWED_TRACKED_MJS_EXACT
    ]

    has_errors = False
    if forbidden_env:
        has_errors = True
        print("ERROR: Forbidden tracked env file(s) found:")
        for path in sorted(forbidden_env):
            print(f"  - {path}")
        print("\nOnly .env.example files are allowed in git.")

    if forbidden_tmp:
        has_errors = True
        if forbidden_env:
            print()
        print("ERROR: Forbidden tracked temp file(s) found:")
        for path in sorted(forbidden_tmp):
            print(f"  - {path}")
        print("\nTemporary artifacts (*.tmp, .tmp*, tmp/) must not be tracked.")

    if forbidden_mjs:
        has_errors = True
        if forbidden_env or forbidden_tmp:
            print()
        print("ERROR: Forbidden tracked .mjs file(s) found:")
        for path in sorted(forbidden_mjs):
            print(f"  - {path}")
        print("\nOnly allowlisted .mjs files may be tracked.")
        print("Allowlisted paths:")
        for path in sorted(ALLOWED_TRACKED_MJS_EXACT):
            print(f"  - {path}")

    if has_errors:
        return 1

    print("OK: Release hygiene checks passed (.env/.tmp/.mjs policies).")
    return 0


def do_metadata(args: argparse.Namespace) -> int:
    _ = args
    pyproject_version = _read_pyproject_version()
    init_version = _read_init_version()

    if pyproject_version != init_version:
        print(
            "ERROR: Version mismatch: "
            f"pyproject.toml={pyproject_version} vs __init__.py={init_version}"
        )
        return 1

    if not _changelog_has_version(pyproject_version):
        print(f"ERROR: CHANGELOG.md is missing release header for {pyproject_version}")
        return 1

    openapi_ok, openapi_message = _check_openapi_contract(pyproject_version)
    print(openapi_message)
    if not openapi_ok:
        return 1

    print(
        "OK: Release metadata is consistent "
        f"(version={pyproject_version}, changelog header present)."
    )
    return 0


def do_wheel(args: argparse.Namespace) -> int:
    try:
        wheel_path = args.wheel or _latest_wheel(args.dist_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not wheel_path.exists():
        print(f"ERROR: Wheel not found at {wheel_path}", file=sys.stderr)
        return 1

    try:
        local_hashes = _collect_local_frontend(args.frontend_dist)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    wheel_hashes, stray_frontend_paths = _collect_wheel_frontend(wheel_path)

    if not wheel_hashes:
        print(
            "ERROR: Wheel does not contain packaged UI assets under fleet_rlm/ui/dist/",
            file=sys.stderr,
        )
        return 1

    if stray_frontend_paths:
        print(
            "ERROR: Wheel contains unexpected frontend package payload paths:",
            file=sys.stderr,
        )
        for path in sorted(stray_frontend_paths):
            print(f"  - {path}", file=sys.stderr)
        return 1

    local_set = set(local_hashes)
    wheel_set = set(wheel_hashes)
    missing_in_wheel = sorted(local_set - wheel_set)
    extra_in_wheel = sorted(wheel_set - local_set)
    mismatched_hashes = sorted(
        path
        for path in local_set & wheel_set
        if local_hashes[path] != wheel_hashes[path]
    )

    if missing_in_wheel or extra_in_wheel or mismatched_hashes:
        print(
            "ERROR: Wheel UI assets are not synchronized with src/frontend/dist.",
            file=sys.stderr,
        )
        if missing_in_wheel:
            print("Missing in wheel:", file=sys.stderr)
            for path in missing_in_wheel:
                print(f"  - {path}", file=sys.stderr)
        if extra_in_wheel:
            print("Extra in wheel:", file=sys.stderr)
            for path in extra_in_wheel:
                print(f"  - {path}", file=sys.stderr)
        if mismatched_hashes:
            print("Content hash mismatch:", file=sys.stderr)
            for path in mismatched_hashes:
                print(f"  - {path}", file=sys.stderr)
        return 1

    print(
        "OK: Wheel frontend assets are present, clean, and synchronized with "
        "src/frontend/dist."
    )
    print(f"Checked wheel: {wheel_path}")
    print(f"Asset count: {len(wheel_hashes)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fleet RLM release validation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_hygiene = subparsers.add_parser(
        "hygiene", help="Check release hygiene (.env/.tmp/.mjs policies)"
    )
    parser_hygiene.set_defaults(func=do_hygiene)

    parser_metadata = subparsers.add_parser(
        "metadata", help="Check release metadata and OpenAPI consistency"
    )
    parser_metadata.set_defaults(func=do_metadata)

    parser_wheel = subparsers.add_parser(
        "wheel", help="Check wheel frontend assets against local dist"
    )
    parser_wheel.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Optional explicit wheel path. Defaults to latest dist/fleet_rlm-*.whl.",
    )
    parser_wheel.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("dist"),
        help="Directory containing built wheel artifacts (default: dist).",
    )
    parser_wheel.add_argument(
        "--frontend-dist",
        type=Path,
        default=Path("src/frontend/dist"),
        help="Frontend dist directory to compare against (default: src/frontend/dist).",
    )
    parser_wheel.set_defaults(func=do_wheel)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
