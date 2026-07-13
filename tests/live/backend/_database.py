"""Fresh-database migration helper for exact-tip live gates."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config


def upgrade_to_head(database_url: str) -> None:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    with patch.dict(os.environ, {"FLEET_DATABASE_URL": database_url}):
        command.upgrade(config, "head")
