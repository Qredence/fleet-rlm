"""QRE-81 public runtime-profile contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.config import Settings


def test_public_runtime_profiles_are_exactly_daytona_and_deno() -> None:
    assert Settings(run_environment="daytona").run_environment == "daytona"
    assert Settings(run_environment="deno").run_environment == "deno"

    with pytest.raises(ValidationError):
        Settings(run_environment="hermetic")  # type: ignore[arg-type]


def test_daytona_is_the_default_public_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    assert Settings(_env_file=None).run_environment == "daytona"


def test_composition_is_split_into_explicit_runtime_and_testing_modules() -> None:
    from fleet_rlm.composition import common, daytona, deno, testing

    assert callable(common.clear_composition_state)
    assert callable(daytona.install_daytona_composition)
    assert callable(deno.install_deno_composition)
    assert callable(testing.create_testing_app)


def test_retained_pre_split_composition_imports_alias_canonical_symbols() -> None:
    import fleet_rlm.composition as composition

    aliases = {
        "LiveCompositionError": "CompositionError",
        "LiveCompositionHandles": "DaytonaCompositionHandles",
        "build_live_composition": "build_daytona_composition",
        "dispose_live_composition": "dispose_daytona_composition",
        "install_live_composition": "install_daytona_composition",
        "require_live_settings": "require_daytona_settings",
        "_clear_composition_state": "clear_composition_state",
    }
    for legacy_name, canonical_name in aliases.items():
        assert getattr(composition, legacy_name, None) is getattr(composition, canonical_name)

    assert not hasattr(composition, "install_offline_composition")
