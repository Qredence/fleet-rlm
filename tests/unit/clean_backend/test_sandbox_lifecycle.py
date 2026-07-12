"""B8: provider get/state matrix and root Sandbox retention defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fleet_rlm_clean.daytona.errors import (
    ProviderRequestError,
    is_sandbox_not_found,
    map_provider_error,
)
from fleet_rlm_clean.daytona.lifecycle import normalize_state
from fleet_rlm_clean.daytona.platform import LiveDaytonaPlatform


class DaytonaNotFoundError(Exception):
    """Name-matched stand-in for the SDK not-found type."""

    def __init__(self, message: str = "missing", status_code: int = 404) -> None:
        super().__init__(message)
        self.status_code = status_code


class _AuthError(Exception):
    def __init__(self) -> None:
        super().__init__("unauthorized")
        self.status_code = 401


def test_normalize_state_unknown_is_unrecoverable() -> None:
    assert normalize_state("running") == "running"
    assert normalize_state("stopped") == "stopped"
    assert normalize_state("error") == "unrecoverable"
    assert normalize_state("booting") == "unrecoverable"
    assert normalize_state("weird-positive") == "unrecoverable"
    assert normalize_state(None) == "missing"
    assert normalize_state("") == "missing"


def test_is_sandbox_not_found_only_for_explicit_missing() -> None:
    assert is_sandbox_not_found(DaytonaNotFoundError()) is True
    assert is_sandbox_not_found(_AuthError()) is False
    assert is_sandbox_not_found(TimeoutError("timed out")) is False
    assert is_sandbox_not_found(RuntimeError("boom")) is False


def test_map_provider_error_non_missing_is_provider_request_error() -> None:
    mapped = map_provider_error(_AuthError())
    assert isinstance(mapped, ProviderRequestError)
    assert mapped.cause_type == "_AuthError"


def test_live_platform_get_none_only_on_not_found() -> None:
    client = MagicMock()
    client.get.side_effect = DaytonaNotFoundError()
    platform = LiveDaytonaPlatform(client)
    assert platform.get("sb-missing") is None


def test_live_platform_get_raises_on_auth_error() -> None:
    client = MagicMock()
    client.get.side_effect = _AuthError()
    platform = LiveDaytonaPlatform(client)
    with pytest.raises(ProviderRequestError):
        platform.get("sb-1")


def test_live_platform_create_defaults_ephemeral_false(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Params:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _Mount:
        def __init__(self, **kwargs):
            del kwargs

    import daytona as daytona_mod

    monkeypatch.setattr(daytona_mod, "CreateSandboxFromSnapshotParams", _Params)
    monkeypatch.setattr(daytona_mod, "VolumeMount", _Mount)

    client = MagicMock()
    client.create.side_effect = lambda params: SimpleNamespace(id="sb-new", params=params)
    platform = LiveDaytonaPlatform(client)
    platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"workspace_id": "ws"},
    )
    assert captured.get("ephemeral") is False
    client.create.assert_called_once()
