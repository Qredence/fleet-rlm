from __future__ import annotations

import pytest

from fleet_rlm.daytona.diagnostics import (
    MissingImportOutcome,
    normalize_missing_import_observation,
)
from fleet_rlm.daytona.runtime import DaytonaEnvironmentProfile


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
    [""],
)
def test_missing_import_observation_rejects_unbounded_or_non_module_names(module: str) -> None:
    with pytest.raises(ValueError, match="normalized import name"):
        normalize_missing_import_observation(module, DaytonaEnvironmentProfile.SESSION)


def test_missing_import_defaults_to_bounded_missing_outcome() -> None:
    result = normalize_missing_import_observation("fleet.tools", DaytonaEnvironmentProfile.WORKSPACE_CHILD)
    assert result.outcome is MissingImportOutcome.MISSING
