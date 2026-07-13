#!/usr/bin/env python3
"""Upgrade an empty Fleet RLM database to the canonical Alembic head."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--database-url", help="Overrides FLEET_DATABASE_URL for this process")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    load_dotenv(args.env_file or root / ".env", override=False)
    database_url = args.database_url or os.getenv("FLEET_DATABASE_URL")
    if not database_url:
        print("FLEET_DATABASE_URL is required", file=sys.stderr)
        return 1

    os.environ["FLEET_DATABASE_URL"] = database_url
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    print("Fleet RLM database upgraded to Alembic head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
