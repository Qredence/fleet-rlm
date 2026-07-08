from __future__ import annotations

import pytest

from fleet_rlm.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillNotVisibleError,
    SkillResourcePathError,
    SkillValidationError,
)
from fleet_rlm.skills.validator import require_valid_resource_path, safe_skill_name


def test_safe_skill_name_raises_skill_validation_error() -> None:
    with pytest.raises(SkillValidationError) as exc_info:
        safe_skill_name("../escape")
    assert exc_info.value.code == "invalid_skill_name"


def test_require_valid_resource_path_raises_skill_resource_path_error() -> None:
    with pytest.raises(SkillResourcePathError) as exc_info:
        require_valid_resource_path("../SKILL.md")
    assert exc_info.value.code == "traversal"


def test_skill_errors_are_value_errors_for_runtime_compat() -> None:
    assert issubclass(SkillNotFoundError, ValueError)
    assert isinstance(SkillNotVisibleError("hidden"), SkillError)
