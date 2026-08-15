"""Daytona client construction contracts, validated against the declared pin."""

from __future__ import annotations

import tomllib
import warnings
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from pydantic import SecretStr

from fleet_rlm.config import Settings
from fleet_rlm.daytona.platform import build_daytona_client


def _declared_daytona_spec() -> SpecifierSet:
    """The ``daytona`` version specifier declared in ``[project].dependencies``."""
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps: list[str] = data["project"]["dependencies"]
    spec = next(dep.strip() for dep in deps if dep.strip().lower().startswith("daytona"))
    return SpecifierSet(spec.removeprefix("daytona").strip())


@pytest.mark.asyncio
async def test_build_daytona_client_uses_explicit_api_url_without_deprecation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_URL", "https://ambient.example/api")
    settings = Settings(
        _env_file=None,
        daytona_api_key=SecretStr("test-daytona-key"),
        daytona_org_id="test-org",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = build_daytona_client(settings)

    try:
        # Guard: run against the exact SDK version declared in pyproject.toml so
        # a dependency bump re-validates this deprecation contract instead of
        # the test hard-failing on a stale version string.
        assert version("daytona") in _declared_daytona_spec()
        assert client._api_url == "https://app.daytona.io/api"
        assert client._api_client.default_headers["X-Daytona-Organization-ID"] == "test-org"
        assert not any("server_url" in str(item.message) for item in caught)
    finally:
        await client.close()
