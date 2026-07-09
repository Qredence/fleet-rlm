from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.skills.errors import SkillNotFoundError, SkillNotVisibleError, SkillResourcePathError
from fleet_rlm.skills.permissions import is_skill_script_execution_permitted
from fleet_rlm.skills.schemas import SkillMetadata, SkillPermissionMode, SkillScope, SkillTrustLevel
from fleet_rlm.skills.script_execution import validate_skill_script_request
from fleet_rlm.skills.service import (
    INACCESSIBLE_SCRIPT_MESSAGE,
    INVALID_SCRIPT_PATH_MESSAGE,
    SCRIPT_NOT_PERMITTED_MESSAGE,
    run_skill_script_error_output,
)


def _metadata(*, scope: SkillScope, trust_level: SkillTrustLevel = SkillTrustLevel.TRUSTED) -> SkillMetadata:
    return SkillMetadata(
        name="alpha",
        description="Alpha skill",
        scope=scope,
        trust_level=trust_level,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source=f"{scope.value}:alpha",
        directory_style=True,
    )


def test_is_skill_script_execution_permitted_builtin_and_admin() -> None:
    assert is_skill_script_execution_permitted(_metadata(scope=SkillScope.SCAFFOLD))
    assert is_skill_script_execution_permitted(_metadata(scope=SkillScope.SYSTEM))
    assert is_skill_script_execution_permitted(_metadata(scope=SkillScope.ORG))
    assert is_skill_script_execution_permitted(_metadata(scope=SkillScope.PROJECT))


def test_is_skill_script_execution_permitted_rejects_user_and_session() -> None:
    assert not is_skill_script_execution_permitted(_metadata(scope=SkillScope.USER))
    assert not is_skill_script_execution_permitted(_metadata(scope=SkillScope.SESSION))


def test_is_skill_script_execution_permitted_rejects_community_trust() -> None:
    assert not is_skill_script_execution_permitted(
        _metadata(scope=SkillScope.SYSTEM, trust_level=SkillTrustLevel.COMMUNITY)
    )


def test_run_skill_script_error_output_sanitizes_hidden_skill() -> None:
    payload = run_skill_script_error_output(SkillNotVisibleError("diagnostics")).model_dump()
    assert payload["error"] == INACCESSIBLE_SCRIPT_MESSAGE
    assert "diagnostics" not in str(payload)


def test_run_skill_script_error_output_maps_path_errors() -> None:
    payload = run_skill_script_error_output(SkillResourcePathError("bad", code="traversal")).model_dump()
    assert payload["error"] == INVALID_SCRIPT_PATH_MESSAGE


def test_run_skill_script_error_output_maps_permission_denial() -> None:
    from fleet_rlm.skills.errors import SkillScriptNotPermittedError

    payload = run_skill_script_error_output(SkillScriptNotPermittedError()).model_dump()
    assert payload["error"] == SCRIPT_NOT_PERMITTED_MESSAGE


def _write_system_skill_with_script(volume: Path, name: str, script_name: str = "run.py") -> None:
    skill_dir = volume / "skills" / "system" / name
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "System skill"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    scripts.joinpath(script_name).write_text("print('ok')\n", encoding="utf-8")


def test_validate_skill_script_request_rejects_non_selected_skill(tmp_path: Path) -> None:
    from fleet_rlm.skills.schemas import SkillRuntimeContext

    volume = tmp_path / "memory"
    _write_system_skill_with_script(volume, "alpha")
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        selected_skill_ids=[],
    )
    with pytest.raises(SkillNotFoundError):
        validate_skill_script_request("alpha", "scripts/run.py", context=context)
