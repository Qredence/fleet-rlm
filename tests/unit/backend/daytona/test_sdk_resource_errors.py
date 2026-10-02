"""Exact SDK resource errors preserve the owning operation's meaning."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from daytona.common.errors import (
    DaytonaAuthenticationError,
    DaytonaBadRequestError,
    DaytonaConflictError,
    DaytonaConnectionError,
    DaytonaFileNotFoundError,
    DaytonaForbiddenError,
    DaytonaInternalServerError,
    DaytonaNotFoundError,
    DaytonaProcessNotFoundError,
    DaytonaRateLimitError,
)

from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    is_sandbox_not_found,
    map_provider_error,
    sanitize_provider_message,
)
from fleet_rlm.daytona.runtime import LiveDaytonaVolumeClient


@pytest.mark.parametrize(
    "error",
    [
        DaytonaFileNotFoundError("missing", status_code=404),
        DaytonaProcessNotFoundError("missing", status_code=404),
        DaytonaNotFoundError("missing context", status_code=404, source="DAYTONA_DAEMON"),
    ],
)
def test_toolbox_absence_is_not_sandbox_absence(error):
    assert not is_sandbox_not_found(error)
    assert not is_sandbox_not_found(map_provider_error(error))


def test_control_plane_sandbox_absence_is_recognized():
    assert is_sandbox_not_found(DaytonaNotFoundError("missing", status_code=404, source="DAYTONA_API"))


# Observed live on 2026-10-01: two concurrent creates of one fresh Volume name
# failed the loser with HTTP 500 or 400 "already exists", not only 409.
_CREATE_RACE_ERRORS = [
    DaytonaConflictError("already exists", status_code=409),
    DaytonaBadRequestError("Volume with name shared already exists", status_code=400),
    DaytonaInternalServerError("An unexpected error occurred.", status_code=500),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", _CREATE_RACE_ERRORS)
async def test_volume_creation_race_reconciles_without_another_create(error):
    volume = SimpleNamespace(id="volume", state="ready")
    get = AsyncMock(side_effect=[error, volume])
    adapter = LiveDaytonaVolumeClient(SimpleNamespace(volume=SimpleNamespace(get=get)))
    assert await adapter.get("shared", create=True) is volume
    assert get.call_args_list == [call("shared", create=True), call("shared", create=False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", _CREATE_RACE_ERRORS)
async def test_volume_creation_failure_without_winner_keeps_the_create_error(error):
    get = AsyncMock(side_effect=[error, DaytonaNotFoundError("missing", status_code=404)])
    adapter = LiveDaytonaVolumeClient(SimpleNamespace(volume=SimpleNamespace(get=get)))
    with pytest.raises(DaytonaAdapterError) as caught:
        await adapter.get("shared", create=True)
    assert caught.value.__cause__ is error
    assert get.call_args_list == [call("shared", create=True), call("shared", create=False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        DaytonaAuthenticationError,
        DaytonaForbiddenError,
        DaytonaRateLimitError,
        DaytonaConnectionError,
    ],
)
async def test_volume_failures_are_normalized_without_retry(error_type):
    get = AsyncMock(side_effect=error_type("token=sentinel /private/provider"))
    adapter = LiveDaytonaVolumeClient(SimpleNamespace(volume=SimpleNamespace(get=get)))
    with pytest.raises(DaytonaAdapterError) as caught:
        await adapter.get("shared", create=True)
    assert "sentinel" not in str(caught.value)
    assert "/private/provider" not in str(caught.value)
    assert get.await_count == 1


def test_provider_error_redaction_covers_json_quoted_credentials() -> None:
    message = sanitize_provider_message('{"api_key":"secret-value","token": "another-secret"}')
    assert "secret-value" not in message
    assert "another-secret" not in message
