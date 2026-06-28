"""Root test configuration — markers, env isolation, fixture registration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Prevent remote model-cost fetch during test collection.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

# Disable MLflow and PostHog by default during all test runs to prevent outbound network hangs/connections.
os.environ.setdefault("MLFLOW_ENABLED", "false")
os.environ.setdefault("POSTHOG_ENABLED", "false")

# Register fixture packages.
pytest_plugins = (
    "tests.fixtures.app",
    "tests.fixtures.daytona",
    "tests.fixtures.agent",
    "tests.fixtures.env",
)


def _suite_from_path(path: Path) -> str | None:
    """Derive the test-suite marker from the file path."""
    parts = path.parts
    if "tests" not in parts:
        return None
    idx = parts.index("tests")
    if idx + 1 >= len(parts):
        return None
    suite = parts[idx + 1]
    if suite in {"unit", "integration", "contracts", "e2e"}:
        return suite
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-apply suite markers and skip live tests unless opted-in."""
    _ = config
    for item in items:
        item_path = Path(str(item.fspath))
        suite = _suite_from_path(item_path)
        if suite is not None:
            item.add_marker(getattr(pytest.mark, suite))

        # Auto-mark DB-dependent integration tests.
        if suite == "integration" and item_path.name.startswith("test_db_"):
            item.add_marker(pytest.mark.db)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Canonical debug-auth headers for local/dev API route tests."""
    return {
        "X-Debug-Tenant-Id": "tenant-a",
        "X-Debug-User-Id": "user-a",
        "X-Debug-Email": "alice@example.com",
        "X-Debug-Name": "Alice",
    }
