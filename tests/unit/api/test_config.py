"""Unit tests for API config utilities."""

from __future__ import annotations

from fleet_rlm.api.config import resolve_server_volume_name
from fleet_rlm.integrations.config.env import AppConfig


def test_resolve_server_volume_name_defaults_to_persistent_volume() -> None:
    """Default volume name should be 'rlm-volume-dspy' when not configured."""
    config = AppConfig()
    assert resolve_server_volume_name(config) == "rlm-volume-dspy"


def test_resolve_server_volume_name_preserves_configured_volume() -> None:
    """Configured volume name should be preserved when set."""
    config = AppConfig(
        volumes={"name": "custom-volume"},
    )
    assert resolve_server_volume_name(config) == "custom-volume"
