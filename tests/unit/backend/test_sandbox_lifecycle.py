"""B8: provider get/state matrix and root Sandbox retention defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fleet_rlm.daytona.errors import (
    ProviderRequestError,
    classify_provider_error,
    is_sandbox_not_found,
    is_transient_provider_failure,
    map_provider_error,
    provider_status_category,
    sanitize_failure_text,
    sanitize_provider_message,
)
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient, normalize_state
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec

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


def test_sanitize_failure_text_types_and_redacts_exception() -> None:
    text = sanitize_failure_text(RuntimeError("provider down api_key=super-secret path=/tmp/private"))

    assert text.startswith("RuntimeError: provider down ")
    assert "super-secret" not in text
    assert "/tmp/private" not in text
    assert text.count("[redacted]") == 2


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        # Transient shapes kept identical to the retired broker-local check.
        (SimpleNamespace(status_code=503), True),
        (TimeoutError("timed out"), True),
        (ConnectionError("offline"), True),
        (ProviderRequestError("502 Bad Gateway", cause_type="DaytonaError"), True),
        (
            ProviderRequestError("HTTP 503 Service Unavailable api_key=sk-secret", cause_type="PreviewLinkError"),
            True,
        ),
        # Non-transient shapes never retry.
        (_AuthError(), False),
        (SimpleNamespace(status_code=422), False),
        (
            ProviderRequestError(
                "Total disk limit exceeded. Upgrade your organization's Tier.",
                cause_type="DaytonaValidationError",
                status_code=400,
            ),
            False,
        ),
        (ProviderRequestError("mount", cause_type="WorkspaceMountMismatch"), False),
        (ProviderRequestError("404 sandbox missing", cause_type="DaytonaError"), False),
        (ProviderRequestError("processed 15001 objects", cause_type="DaytonaError"), False),
        (RuntimeError("other"), False),
    ],
)
def test_is_transient_provider_failure(exc: object, expected: bool) -> None:
    assert is_transient_provider_failure(exc) is expected


@pytest.mark.asyncio
async def test_live_platform_get_none_only_on_not_found() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=DaytonaNotFoundError())
    platform = LiveDaytonaPlatform(client, _SPEC)
    assert await platform.get("sb-missing") is None


@pytest.mark.asyncio
async def test_live_platform_stop_treats_missing_sandbox_as_already_stopped() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=DaytonaNotFoundError())
    client.stop = AsyncMock()
    client.delete = AsyncMock()
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.stop("sb-missing", force=True)

    client.stop.assert_not_awaited()
    client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_platform_get_raises_on_auth_error() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=_AuthError())
    platform = LiveDaytonaPlatform(client, _SPEC)
    with pytest.raises(ProviderRequestError):
        await platform.get("sb-1")


@pytest.mark.asyncio
async def test_live_platform_passes_strict_ephemeral_network_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Params:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class _Client:
        async def create(self, params: object) -> object:
            return params

    monkeypatch.setattr("daytona.CreateSandboxFromSnapshotParams", _Params)
    platform = LiveDaytonaPlatform(_Client(), _SPEC)
    await platform.create(
        with_volume=False,
        ephemeral=True,
        network_block_all=True,
        network_allow_list="10.0.0.0/8",
        domain_allow_list="gateway.example.test",
    )

    assert captured["volumes"] is None
    assert captured["ephemeral"] is True
    assert captured["network_block_all"] is True
    assert captured["network_allow_list"] == "10.0.0.0/8"
    assert captured["domain_allow_list"] == "gateway.example.test"
    assert captured["auto_stop_interval"] is None
    assert captured["auto_delete_interval"] is None


@pytest.mark.asyncio
async def test_live_volume_client_waits_for_created_volume_to_be_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.volume.get = AsyncMock(
        side_effect=[
            SimpleNamespace(id="vol-1", state="creating"),
            SimpleNamespace(id="vol-1", state="ready"),
        ]
    )

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("fleet_rlm.daytona.platform.asyncio.sleep", _no_sleep)
    volume = await LiveDaytonaVolumeClient(client).get("vol-1", create=True)
    assert volume.state == "ready"
    assert client.volume.get.call_args_list == [
        (("vol-1",), {"create": True}),
        (("vol-1",), {"create": False}),
    ]


@pytest.mark.asyncio
async def test_live_volume_client_rejects_failed_volume_state() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError

    client = MagicMock()
    client.volume.get = AsyncMock(side_effect=[SimpleNamespace(id="vol-1", state="error")])

    with pytest.raises(DaytonaAdapterError, match="did not become ready"):
        await LiveDaytonaVolumeClient(client).get("vol-1", create=True)


@pytest.mark.asyncio
async def test_live_platform_delete_resolves_id_for_daytona_async_contract() -> None:
    client = MagicMock()
    sandbox = SimpleNamespace(id="sb-1")
    client.get = AsyncMock(return_value=sandbox)
    client.delete = AsyncMock()

    await LiveDaytonaPlatform(client, _SPEC).delete("sb-1")

    client.get.assert_called_once_with("sb-1")
    client.delete.assert_called_once_with(sandbox)


@pytest.mark.asyncio
async def test_live_platform_start_and_stop_use_async_client_methods() -> None:
    client = MagicMock()
    sandbox = MagicMock()
    client.get = AsyncMock(return_value=sandbox)
    client.start = AsyncMock()
    client.stop = AsyncMock()
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.start("sb-1")
    await platform.stop("sb-1", timeout=12, force=True)

    assert client.get.await_args_list == [(("sb-1",), {}), (("sb-1",), {})]
    client.start.assert_awaited_once_with(sandbox)
    client.stop.assert_awaited_once_with(sandbox, timeout=12)


@pytest.mark.asyncio
async def test_live_platform_force_stop_deletes_sandbox_when_stop_fails() -> None:
    client = MagicMock()
    sandbox = MagicMock()
    client.get = AsyncMock(return_value=sandbox)
    client.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    client.delete = AsyncMock()

    await LiveDaytonaPlatform(client, _SPEC).stop("sb-1", force=True)

    client.delete.assert_awaited_once_with(sandbox)


@pytest.mark.asyncio
async def test_live_platform_create_defaults_ephemeral_false(monkeypatch: pytest.MonkeyPatch) -> None:
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
    client.create = AsyncMock(side_effect=lambda params: SimpleNamespace(id="sb-new", params=params))
    platform = LiveDaytonaPlatform(client, _SPEC)
    await platform.create(
        volume_id="vol-1",
        mount_path="/home/daytona/fleet",
        volume_subpath="workspaces/11111111-1111-1111-1111-111111111111",
        labels={"workspace_id": "ws"},
    )
    assert captured.get("ephemeral") is False
    client.create.assert_called_once()


@pytest.mark.asyncio
async def test_live_platform_create_matches_daytona_async_payload_contract() -> None:
    """Pin the scoped mount payload consumed by the asynchronous Daytona SDK."""
    client = MagicMock()
    client.create = AsyncMock(side_effect=lambda params: SimpleNamespace(id="sb-new", params=params))
    platform = LiveDaytonaPlatform(client, _SPEC)

    await platform.create(
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
