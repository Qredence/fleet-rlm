"""Behavioral checks for legacy compatibility memory tool."""

from __future__ import annotations

from fleet_rlm.core.memory_tools import search_evolutive_memory


def test_search_evolutive_memory_returns_deterministic_deprecation_message() -> None:
    result = search_evolutive_memory("find onboarding notes")

    assert "[LEGACY MEMORY DEPRECATED]" in result
    assert "Query received:" in result
    assert "find onboarding notes" in result
