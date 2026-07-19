"""B8: provider get/state matrix and root Sandbox retention defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fleet_rlm.daytona.errors import (
    ProviderRequestError,
    classify_provider_error,
    is_sandbox_not_found,
    map_provider_error,
    provider_status_category,
    sanitize_provider_message,
)
from fleet_rlm.daytona.lifecycle import normalize_state
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient
from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec

_SPEC = DaytonaSandboxSpec("fleet-test-v1")


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


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_AuthError(), "auth"),
        (SimpleNamespace(status_code=429), "quota"),
        (
            ProviderRequestError(
                "Total disk limit exceeded. Upgrade your organization's Tier.",
                cause_type="DaytonaValidationError",
                status_code=400,
            ),
            "quota",
        ),
        (TimeoutError("slow"), "timeout"),
        (ConnectionError("offline"), "network"),
        (OSError("dns unavailable"), "network"),
        (SimpleNamespace(status_code=503), "provider_5xx"),
        (SimpleNamespace(status_code=422), "request_validation"),
        (ProviderRequestError("mount", cause_type="WorkspaceMountMismatch"), "mount_mismatch"),
        (ProviderRequestError("interp", cause_type="InterpreterLifecycleError"), "interpreter"),
        (RuntimeError("other"), "unknown"),
    ],
)
def test_provider_error_classification(exc: object, expected: str) -> None:
    assert classify_provider_error(exc) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [(None, "none"), (401, "4xx"), (429, "4xx"), (503, "5xx")],
)
def test_provider_status_category(status: int | None, expected: str) -> None:
    assert provider_status_category(status) == expected


def test_sanitize_provider_message_redacts_secrets_and_private_paths() -> None:
    sanitized = sanitize_provider_message(
        "api_key=super-secret Bearer private-token at /Users/zach/project/.env and /Volumes/SSD/key.txt"
    )

    assert "super-secret" not in sanitized
    assert "private-token" not in sanitized
    assert "/Users/zach" not in sanitized
    assert "/Volumes/SSD" not in sanitized
    assert sanitized.count("[redacted]") >= 4


def test_live_platform_get_none_only_on_not_found() -> None:
    client = MagicMock()
    client.get.side_effect = DaytonaNotFoundError()
    platform = LiveDaytonaPlatform(client, _SPEC)
    assert platform.get("sb-missing") is None


def test_live_platform_get_raises_on_auth_error() -> None:
    client = MagicMock()
    client.get.side_effect = _AuthError()
    platform = LiveDaytonaPlatform(client, _SPEC)
    with pytest.raises(ProviderRequestError):
        platform.get("sb-1")


def test_live_volume_client_waits_for_created_volume_to_be_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.volume.get.side_effect = [
        SimpleNamespace(id="vol-1", state="creating"),
        SimpleNamespace(id="vol-1", state="ready"),
    ]
    monkeypatch.setattr("fleet_rlm.daytona.platform.time.sleep", lambda _delay: None)

    volume = LiveDaytonaVolumeClient(client).get("vol-1", create=True)

    assert volume.state == "ready"
    assert client.volume.get.call_args_list == [
        (("vol-1",), {"create": True}),
        (("vol-1",), {"create": False}),
    ]


def test_live_volume_client_rejects_failed_volume_state() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError

    client = MagicMock()
    client.volume.get.side_effect = [SimpleNamespace(id="vol-1", state="error")]

    with pytest.raises(DaytonaAdapterError, match="did not become ready"):
        LiveDaytonaVolumeClient(client).get("vol-1", create=True)


def test_live_platform_delete_resolves_id_for_daytona_0_192_contract() -> None:
    client = MagicMock()
    sandbox = SimpleNamespace(id="sb-1")
    client.get.return_value = sandbox

    LiveDaytonaPlatform(client, _SPEC).delete("sb-1")

    client.get.assert_called_once_with("sb-1")
    client.delete.assert_called_once_with(sandbox)


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
    platform = LiveDaytonaPlatform(client, _SPEC)
    platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"workspace_id": "ws"},
    )
    assert captured.get("ephemeral") is False
    client.create.assert_called_once()


def test_live_platform_create_matches_daytona_0_192_payload_contract() -> None:
    """Pin the scoped mount payload consumed by Daytona SDK 0.192.0."""
    client = MagicMock()
    client.create.side_effect = lambda params: SimpleNamespace(id="sb-new", params=params)
    platform = LiveDaytonaPlatform(client, _SPEC)

    platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"purpose": "fleet-daytona-doctor"},
        ephemeral=True,
    )

    params = client.create.call_args.args[0]
    assert params.ephemeral is True
    assert params.snapshot == _SPEC.snapshot
    assert params.os_user == "daytona"
    assert params.labels == {"purpose": "fleet-daytona-doctor"}
    assert len(params.volumes) == 1
    assert params.volumes[0].to_dict() == {
        "volumeId": "vol-1",
        "mountPath": "/home/daytona/fleet",
        "subpath": "workspaces/11111111-1111-1111-1111-111111111111",
    }
