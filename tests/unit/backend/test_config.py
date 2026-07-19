"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.config import Settings


def test_startup_rejects_retired_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_LIVE_KERNEL", "true")
    with pytest.raises(ValueError, match="retired Fleet environment variable.*FLEET_LIVE_KERNEL"):
        create_app(settings=Settings(_env_file=None))


def test_startup_rejects_retired_budget_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_BUDGET_MAX_ITERATIONS", "6")
    with pytest.raises(ValueError, match="retired Fleet environment variable.*FLEET_BUDGET_MAX_ITERATIONS"):
        create_app(settings=Settings(_env_file=None))


def test_turn_timeout_defaults_to_thirty_minutes() -> None:
    assert Settings(_env_file=None).turn_timeout_seconds == 1800


def test_daytona_admission_defaults_to_eight_leases() -> None:
    assert Settings(_env_file=None).max_active_daytona_leases == 8


def test_daytona_admission_reads_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_ACTIVE_DAYTONA_LEASES", "3")
    assert Settings(_env_file=None).max_active_daytona_leases == 3


@pytest.mark.parametrize("value", [0, -1, 9])
def test_daytona_admission_must_be_between_one_and_eight(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_active_daytona_leases=value)


def test_turn_timeout_reads_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "1200")
    assert Settings(_env_file=None).turn_timeout_seconds == 1200


@pytest.mark.parametrize("value", [0, -1])
def test_turn_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, turn_timeout_seconds=value)
