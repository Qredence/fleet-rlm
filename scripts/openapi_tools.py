#!/usr/bin/env python3
"""Generate and check the backend-only Fleet RLM OpenAPI contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUTPUT = ROOT / "openapi.yaml"


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
    print(f"Generated {len(schema.get('paths', {}))} backend paths in {OUTPUT}")
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
    if "post" in paths.get("/api/artifacts/{artifact_id}", {}):
        print("Public Artifact creation must not exist", file=sys.stderr)
        return 1
    print(f"Backend OpenAPI is current ({len(paths)} paths)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate").set_defaults(func=generate)
    commands.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
