from __future__ import annotations

import os

import pytest


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


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip tests marked live_llm unless explicitly enabled."""
    if _live_llm_enabled():
        return

    skip_live = pytest.mark.skip(
        reason=(
            "live_llm test skipped by default. "
            "Set RLM_LIVE_TESTS=1 to include live Modal + LM integration tests."
        )
    )
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def require_litellm() -> None:
    """Skip live integration tests when runtime prerequisites are missing."""
    if not _modal_credentials_present():
        pytest.skip("Modal credentials not configured")
    if not _lm_configured():
        pytest.skip("DSPy LM not configured")
    if not check_litellm_secret():
        pytest.skip("LITELLM secret not configured")
