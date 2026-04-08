from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

# Keep test collection deterministic: importing DSPy/LiteLLM via shared fixtures
# should not trigger a remote model-cost fetch.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

pytest_plugins = ("tests.unit.fixtures_react", "tests.unit.fixtures_daytona")


def _suite_from_path(path: Path) -> str | None:
    parts = path.parts
    if "tests" not in parts:
        return None
    tests_index = parts.index("tests")
    if tests_index + 1 >= len(parts):
        return None
    suite = parts[tests_index + 1]
    if suite in {"unit", "ui", "integration", "e2e"}:
        return suite
    return None


def pytest_configure(config: pytest.Config) -> None:
    """Defensively register markers for external tooling and strict mode."""
    marker_docs = {
        "unit": "unit test suite",
        "ui": "UI/server test suite",
        "integration": "integration test suite",
        "db": "database-backed integration tests",
        "e2e": "end-to-end test suite",
        "benchmark": "performance/throughput benchmark tests",
        "live_llm": "tests that require a live configured LM / LiteLLM backend",
        "live_daytona": "tests that require a live Daytona backend and explicit opt-in",
    }
    for marker, description in marker_docs.items():
        config.addinivalue_line("markers", f"{marker}: {description}")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    _ = config
    for item in items:
        item_path = Path(str(item.fspath))
        suite = _suite_from_path(item_path)
        if suite is not None:
            item.add_marker(getattr(pytest.mark, suite))

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


@pytest.fixture
def websocket_auth_headers() -> dict[str, str]:
    """Canonical debug-auth headers for websocket tests using default identity."""
    return {
        "X-Debug-Tenant-Id": "default",
        "X-Debug-User-Id": "alice",
        "X-Debug-Email": "alice@example.com",
        "X-Debug-Name": "Alice",
    }


@pytest.fixture
def reset_server_state() -> Iterator[None]:
    """Compatibility fixture for app-scoped server state tests."""
    yield
