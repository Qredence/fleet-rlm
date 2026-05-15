"""Unit tests for session title helpers."""

from __future__ import annotations

from fleet_rlm.utils.session_titles import is_placeholder_session_title


def test_is_placeholder_session_title_matches_frontend_uuid_rules() -> None:
    """Verify backend placeholder UUID detection stays aligned with the frontend."""
    assert is_placeholder_session_title("40122f3a-41d0-453d-8b60-61caba6fe37b") is True
    assert is_placeholder_session_title("Session 40122f3a-41d0-453d-8b60-61caba6fe37b") is True
    assert is_placeholder_session_title("40122f3a-41d0-053d-8b60-61caba6fe37b") is False
    assert is_placeholder_session_title("Session 40122f3a-41d0-453d-6b60-61caba6fe37b") is False
