"""Integration test fixtures — DB gates and live-service checks."""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture
def require_database_url() -> str:
    database_url = _database_url()
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    return database_url


@pytest_asyncio.fixture
async def database_manager(require_database_url: str) -> AsyncIterator:
    from fleet_rlm.integrations.database import DatabaseManager

    db = DatabaseManager(require_database_url)
    try:
        yield db
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def repository(database_manager) -> object:
    from fleet_rlm.integrations.database import FleetRepository

    return FleetRepository(database_manager)
