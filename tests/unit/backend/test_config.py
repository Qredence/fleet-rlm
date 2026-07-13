"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.config import Settings


def test_max_turn_wall_seconds_defaults_to_fifteen_minutes() -> None:
    assert Settings(_env_file=None).max_turn_wall_seconds == 900


def test_max_turn_wall_seconds_reads_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_TURN_WALL_SECONDS", "1200")
    assert Settings(_env_file=None).max_turn_wall_seconds == 1200


@pytest.mark.parametrize("value", [0, -1])
def test_max_turn_wall_seconds_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_turn_wall_seconds=value)
