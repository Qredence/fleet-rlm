#!/usr/bin/env python3
"""Backend-only release metadata, hygiene, and wheel validation."""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "fleet_rlm" / "__init__.py"
OPENAPI = ROOT / "openapi.yaml"
CHANGELOG = ROOT / "CHANGELOG.md"
REQUIRED_WHEEL_FILES = {
    "fleet_rlm/__init__.py",
    "fleet_rlm/app.py",
    "fleet_rlm/main.py",
    "fleet_rlm/py.typed",
    "fleet_rlm/daytona/snapshot-requirements.txt",
    "fleet_rlm/api/dependencies.py",
    "fleet_rlm/chat/turn_coordinator.py",
    "fleet_rlm/daytona/workspace_agent_runtime.py",
    "fleet_rlm/daytona/workspace_gateway.py",
    "fleet_rlm/persistence/models.py",
    "fleet_rlm/rlm/runner.py",
    "fleet_rlm/skills/bundled/data-analysis/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/references/rlm-contract.md",
    "fleet_rlm/skills/bundled/long-context/SKILL.md",
    "fleet_rlm/skills/bundled/long-context/references/chunking-strategies.md",
    "fleet_rlm/skills/bundled/long-context/scripts/rank_chunks.py",
    "fleet_rlm/skills/bundled/long-context/scripts/semantic_chunk.py",
    "fleet_rlm/skills/bundled/workspace-files/SKILL.md",
    "fleet_rlm/skills/bundled/workspace-files/references/filesystem-contract.md",
    "fleet_rlm/skills/bundled/report-builder/SKILL.md",
}


def _project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _package_version() -> str:
    spec = importlib.util.spec_from_file_location("fleet_rlm_release_probe", INIT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fleet_rlm.__init__")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.__version__)


def metadata(_args: argparse.Namespace) -> int:
    project_version = _project_version()
    package_version = _package_version()
    schema = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    openapi_version = str(schema.get("info", {}).get("version", ""))
    if len({project_version, package_version, openapi_version}) != 1:
        print(
            f"ERROR: version drift project={project_version} package={package_version} openapi={openapi_version}",
            file=sys.stderr,
        )
        return 1
    changelog = CHANGELOG.read_text(encoding="utf-8")
    if not re.search(rf"^## (?:\[)?{re.escape(project_version)}(?:\])? - ", changelog, re.MULTILINE):
        print(f"ERROR: CHANGELOG has no {project_version} release heading", file=sys.stderr)
        return 1
    print(f"OK: backend release metadata is aligned at {project_version}")
    return 0


def hygiene(_args: argparse.Namespace) -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    violations = [
        path
        for path in tracked
        if path
        and (
            (re.search(r"(^|/)\.env(?:\..+)?$", path) and not path.endswith(".env.example"))
            or path.endswith((".tmp", ".swp"))
            or "__pycache__" in path
        )
    ]
    if violations:
        print("ERROR: forbidden tracked files:\n" + "\n".join(violations), file=sys.stderr)
        return 1
    print("OK: tracked-file hygiene passed")
    return 0


def wheel(args: argparse.Namespace) -> int:
    wheels = sorted(args.dist_dir.glob("fleet_rlm-*.whl"))
    if not wheels:
        print(f"ERROR: no wheel in {args.dist_dir}", file=sys.stderr)
        return 1
    wheel_path = wheels[-1]
    with zipfile.ZipFile(wheel_path) as archive:
        files = {name for name in archive.namelist() if not name.endswith("/")}
    missing = sorted(REQUIRED_WHEEL_FILES - files)
    forbidden = sorted(
        name
        for name in files
        if name.startswith(("frontend/", "fleet_rlm/ui/", "fleet_rlm/skills/skills/")) or name.endswith(".pdf")
    )
    if missing or forbidden:
        if missing:
            print("ERROR: wheel missing:\n" + "\n".join(missing), file=sys.stderr)
        if forbidden:
            print("ERROR: forbidden wheel payload:\n" + "\n".join(forbidden), file=sys.stderr)
        return 1
    print(f"OK: canonical backend wheel validated: {wheel_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("metadata").set_defaults(func=metadata)
    commands.add_parser("hygiene").set_defaults(func=hygiene)
    wheel_parser = commands.add_parser("wheel")
    wheel_parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    wheel_parser.set_defaults(func=wheel)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
