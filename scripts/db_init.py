#!/usr/bin/env python3
"""Initialize Neon/Postgres schema for fleet-rlm."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from fleet_rlm.integrations.database.engine import DatabaseManager, select_database_url


def build_parser() -> argparse.ArgumentParser:
    """Build and return an ArgumentParser for database initialization.

    Creates a parser that validates DB connectivity and applies Alembic migrations.
    Accepts --env-file (path to dotenv) and --database-url (override DB URL).

    Returns:
        argparse.ArgumentParser: Configured parser for db_init workflow.
    """
    parser = argparse.ArgumentParser(description="Validate database connectivity and apply Alembic migrations")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional dotenv file to load before resolving database settings",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional explicit database URL override. Defaults to DATABASE_ADMIN_URL then DATABASE_URL.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(args.env_file or repo_root / ".env", override=False)

    database_url = args.database_url or select_database_url(
        runtime_url=os.getenv("DATABASE_URL"),
        admin_url=os.getenv("DATABASE_ADMIN_URL"),
        prefer_admin=True,
    )
    if not database_url:
        print("DATABASE_ADMIN_URL or DATABASE_URL is required", file=sys.stderr)
        return 1

    db = DatabaseManager(database_url)

    async def _ping() -> None:
        await db.ping()
        await db.dispose()

    asyncio.run(_ping())

    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(alembic_cfg, "head")

    print("Database connectivity validated and migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
