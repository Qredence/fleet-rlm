#!/usr/bin/env python3
"""Initialize Neon/Postgres schema for fleet-rlm."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from fleet_rlm.integrations.database.engine import DatabaseManager, select_database_url


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env", override=False)

    database_url = select_database_url(
        runtime_url=os.getenv("DATABASE_URL"),
        admin_url=os.getenv("DATABASE_ADMIN_URL"),
        prefer_admin=True,
    )
    if not database_url:
        print("DATABASE_ADMIN_URL or DATABASE_URL is required")
        return 1

    db = DatabaseManager(database_url)

    async def _ping() -> None:
        await db.ping()
        await db.dispose()

    import asyncio

    asyncio.run(_ping())

    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(alembic_cfg, "head")

    print("Database connectivity validated and migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
