from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

from fleet_rlm.daytona_rlm.config import resolve_daytona_config
from fleet_rlm.db import DatabaseManager, FleetRepository

DATABASE_URL = os.getenv("DATABASE_URL")


def _lm_configured() -> bool:
    """Check whether DSPy has an LM configured in current runtime settings."""
    try:
        import dspy

        return dspy.settings.lm is not None
    except Exception:
        return False


def _modal_credentials_present() -> bool:
    """Check whether Modal credentials are available in environment."""
    return bool(os.environ.get("MODAL_TOKEN_ID")) and bool(
        os.environ.get("MODAL_TOKEN_SECRET")
    )


def check_litellm_secret() -> bool:
    """Check whether Modal LITELLM secret is configured and complete."""
    try:
        from fleet_rlm.runners import check_secret_presence

        result = check_secret_presence()
        return all(result.values())
    except Exception:
        return False


def _live_llm_enabled() -> bool:
    """Explicit gate for live LM integration tests."""
    return os.environ.get("RLM_LIVE_TESTS", "0") == "1"


def _live_daytona_enabled() -> bool:
    """Explicit gate for live Daytona integration tests."""
    return os.environ.get("DAYTONA_LIVE_TESTS", "0") == "1"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip tests marked live_llm unless explicitly enabled."""
    for item in items:
        if "live_llm" in item.keywords and not _live_llm_enabled():
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "live_llm test skipped by default. "
                        "Set RLM_LIVE_TESTS=1 to include live Modal + LM integration tests."
                    )
                )
            )
        if "live_daytona" in item.keywords and not _live_daytona_enabled():
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "live_daytona test skipped by default. "
                        "Set DAYTONA_LIVE_TESTS=1 to include live Daytona validation tests."
                    )
                )
            )


@pytest.fixture
def require_litellm() -> None:
    """Skip live integration tests when runtime prerequisites are missing."""
    if not _modal_credentials_present():
        pytest.skip("Modal credentials not configured")
    if not _lm_configured():
        pytest.skip("DSPy LM not configured")
    if not check_litellm_secret():
        pytest.skip("LITELLM secret not configured")


@pytest.fixture
def require_daytona_runtime() -> None:
    """Validate that native Daytona env names are configured for live tests."""
    _ = resolve_daytona_config()


@pytest_asyncio.fixture
async def database_manager() -> AsyncIterator[DatabaseManager]:
    """DB manager fixture for integration tests."""
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not configured")
    db = DatabaseManager(DATABASE_URL)
    try:
        yield db
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def repository(database_manager: DatabaseManager) -> FleetRepository:
    """Repository fixture for DB-backed integration tests."""
    return FleetRepository(database_manager)
