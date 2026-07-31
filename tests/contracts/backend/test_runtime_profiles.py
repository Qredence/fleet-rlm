"""Public Run Environment profile contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.config import Settings


def test_public_runtime_profiles_are_exactly_daytona() -> None:
    assert Settings(run_environment="daytona").run_environment == "daytona"

    with pytest.raises(ValidationError):
        Settings(run_environment="unsupported")  # type: ignore[arg-type]


def test_daytona_is_the_default_public_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    assert Settings(_env_file=None).run_environment == "daytona"
