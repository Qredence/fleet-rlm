#!/usr/bin/env python3
"""Generate or check the checked-in OpenAPI document from the Lite FastAPI app."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fleet_rlm.api.app import create_app  # noqa: E402
from fleet_rlm.config import FleetSettings  # noqa: E402

# The schema depends only on the routes; build it without .env, a database, or MLflow.
app = create_app(settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False)

OPENAPI_PATH = ROOT / "openapi.yaml"


def render_openapi() -> str:
    """Return a deterministic YAML representation of the current API schema."""
    schema = app.openapi()
    validate(schema)
    return yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=100)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    args = parser.parse_args(argv)
    generated = render_openapi()
    if args.command == "generate":
        OPENAPI_PATH.write_text(generated, encoding="utf-8")
        print(f"Generated {OPENAPI_PATH.relative_to(ROOT)}")
        return 0
    if not OPENAPI_PATH.is_file() or OPENAPI_PATH.read_text(encoding="utf-8") != generated:
        print("openapi.yaml is stale; run `make api-sync` and review the contract diff.", file=sys.stderr)
        return 1
    print("openapi.yaml matches the current FastAPI contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
