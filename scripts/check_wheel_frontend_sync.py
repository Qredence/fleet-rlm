#!/usr/bin/env python3
"""Verify wheel frontend assets are present, clean, and synchronized."""

from __future__ import annotations

from argparse import ArgumentParser
import hashlib
from pathlib import Path
import sys
import zipfile


WHEEL_UI_PREFIX = "fleet_rlm/ui/dist/"


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


def main() -> int:
    parser = ArgumentParser(
        description="Check built wheel includes synced frontend assets and no stray frontend package payloads."
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Optional explicit wheel path. Defaults to latest dist/fleet_rlm-*.whl.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("dist"),
        help="Directory containing built wheel artifacts (default: dist).",
    )
    parser.add_argument(
        "--frontend-dist",
        type=Path,
        default=Path("src/frontend/dist"),
        help="Frontend dist directory to compare against (default: src/frontend/dist).",
    )
    args = parser.parse_args()

    wheel_path = args.wheel or _latest_wheel(args.dist_dir)
    if not wheel_path.exists():
        print(f"ERROR: Wheel not found at {wheel_path}", file=sys.stderr)
        return 1

    local_hashes = _collect_local_frontend(args.frontend_dist)
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
        "OK: Wheel frontend assets are present, clean, and synchronized with src/frontend/dist."
    )
    print(f"Checked wheel: {wheel_path}")
    print(f"Asset count: {len(wheel_hashes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
