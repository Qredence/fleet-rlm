from __future__ import annotations

import pytest

from fleet_rlm.daytona.provisioning import (
    DaytonaEnvironmentProfile,
    MissingImportOutcome,
    normalize_missing_import_observation,
)


def test_missing_import_observation_is_normalized_and_content_free() -> None:
    observation = normalize_missing_import_observation("  Fleet.Tools.Parser  ", "semantic-child", "import-error")
    assert observation.as_dict() == {
        "module": "fleet.tools.parser",
        "profile": "semantic-child",
        "outcome": "import-error",
    }
    assert set(observation.as_dict()) == {"module", "profile", "outcome"}


@pytest.mark.parametrize(
    "module",
    ["", "private/path", "module;secret", "a" * 129, "module with spaces"],
)
def test_missing_import_observation_rejects_unbounded_or_non_module_names(module: str) -> None:
    with pytest.raises(ValueError, match="normalized import name"):
        normalize_missing_import_observation(module, DaytonaEnvironmentProfile.SESSION)


def test_missing_import_observation_rejects_unknown_profile_or_outcome() -> None:
    with pytest.raises(ValueError, match="profile or outcome"):
        normalize_missing_import_observation("fleet.tools", "unknown-profile")
    with pytest.raises(ValueError, match="profile or outcome"):
        normalize_missing_import_observation("fleet.tools", DaytonaEnvironmentProfile.SESSION, "failed")


def test_missing_import_defaults_to_bounded_missing_outcome() -> None:
    result = normalize_missing_import_observation("fleet.tools", DaytonaEnvironmentProfile.WORKSPACE_CHILD)
    assert result.outcome is MissingImportOutcome.MISSING
