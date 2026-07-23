#!/usr/bin/env python3
"""Generate and check the backend-only Fleet RLM OpenAPI contract."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUT = ROOT / "openapi.yaml"
TUI_ROOT = ROOT / "tools" / "fleet-tui"
TUI_OUTPUT = TUI_ROOT / "src" / "generated" / "openapi.ts"


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        return True


def _schema() -> dict[str, Any]:
    from fleet_rlm.main import app

    schema = app.openapi()
    schema["openapi"] = "3.1.0"
    return schema


def _render(schema: dict[str, Any]) -> str:
    return yaml.dump(
        schema,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def generate(_args: argparse.Namespace) -> int:
    schema = _schema()
    OUTPUT.write_text(_render(schema), encoding="utf-8")
    TUI_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _generate_typescript(TUI_OUTPUT)
    print(f"Generated {len(schema.get('paths', {}))} backend paths and TUI HTTP types")
    return 0


def check(_args: argparse.Namespace) -> int:
    if not OUTPUT.exists():
        print(f"Missing generated contract: {OUTPUT}", file=sys.stderr)
        return 1
    expected = _render(_schema())
    actual = OUTPUT.read_text(encoding="utf-8")
    if actual != expected:
        print("openapi.yaml is stale; run `make api-sync`", file=sys.stderr)
        return 1

    schema = yaml.safe_load(actual)
    paths = schema.get("paths", {})
    if any(path.startswith("/api/v1") for path in paths):
        print("Legacy /api/v1 path found in backend contract", file=sys.stderr)
        return 1
    if "post" in paths.get("/api/artifacts", {}) or "post" in paths.get("/api/artifacts/{artifact_id}", {}):
        print("Public Artifact creation must not exist", file=sys.stderr)
        return 1
    if any("/stage" in path for path in paths):
        print("Public Attachment stage must not exist", file=sys.stderr)
        return 1

    if not TUI_OUTPUT.exists():
        print("Missing generated TUI HTTP types; run `make api-sync`", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as directory:
        expected_types = Path(directory) / "openapi.ts"
        _generate_typescript(expected_types)
        if TUI_OUTPUT.read_text(encoding="utf-8") != expected_types.read_text(encoding="utf-8"):
            print("TUI HTTP types are stale; run `make api-sync`", file=sys.stderr)
            return 1
    print(f"Backend OpenAPI and TUI HTTP types are current ({len(paths)} paths)")
    return 0


def _generate_typescript(output: Path) -> None:
    subprocess.run(
        (
            "pnpm",
            "exec",
            "openapi-typescript",
            str(OUTPUT),
            "-o",
            str(output),
        ),
        cwd=TUI_ROOT,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=generate)
    commands.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
