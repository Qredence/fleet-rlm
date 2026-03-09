from __future__ import annotations

import pytest

from fleet_rlm.daytona_rlm.config import DaytonaConfigError, resolve_daytona_config


def test_resolve_daytona_config_requires_native_api_url():
    with pytest.raises(DaytonaConfigError, match="DAYTONA_API_URL"):
        resolve_daytona_config(
            {
                "DAYTONA_API_KEY": "key",
                "DAYTONA_API_BASE_URL": "https://api.daytona.example",
            }
        )


def test_resolve_daytona_config_accepts_native_env_names():
    config = resolve_daytona_config(
        {
            "DAYTONA_API_KEY": "key",
            "DAYTONA_API_URL": "https://api.daytona.example",
        }
    )

    assert config.api_key == "key"
    assert config.api_url == "https://api.daytona.example"
    assert config.target is None


def test_resolve_daytona_config_passes_through_target():
    config = resolve_daytona_config(
        {
            "DAYTONA_API_KEY": "key",
            "DAYTONA_API_URL": "https://api.daytona.example",
            "DAYTONA_TARGET": "europe",
        }
    )

    assert config.target == "europe"
