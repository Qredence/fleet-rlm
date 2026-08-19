"""Daytona 0.202.0 client construction contracts."""

from __future__ import annotations

import warnings
from importlib.metadata import version

import pytest
from pydantic import SecretStr

from fleet_rlm.config import Settings
from fleet_rlm.daytona.platform import build_daytona_client


@pytest.mark.asyncio
async def test_build_daytona_client_uses_explicit_api_url_without_deprecation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_URL", "https://ambient.example/api")
    settings = Settings(
        daytona_api_key=SecretStr("test-daytona-key"),
        daytona_org_id="test-org",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = build_daytona_client(settings)

    try:
        assert version("daytona") == "0.202.0"
        assert client._api_url == "https://app.daytona.io/api"
        assert client._api_client.default_headers["X-Daytona-Organization-ID"] == "test-org"
        assert not any("server_url" in str(item.message) for item in caught)
    finally:
        await client.close()
