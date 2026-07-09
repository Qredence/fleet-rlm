from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.skills.errors import SkillNotFoundError, SkillNotVisibleError, SkillResourcePathError
from fleet_rlm.skills.permissions import is_skill_script_execution_permitted
from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillPermissionMode,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
)
from fleet_rlm.skills.script_execution import validate_skill_script_request
from fleet_rlm.skills.service import (
    INACCESSIBLE_SCRIPT_MESSAGE,
    INVALID_SCRIPT_PATH_MESSAGE,
    SCRIPT_NOT_PERMITTED_MESSAGE,
    run_skill_script_error_output,
)
from fleet_rlm.tools.skill_tools import run_skill_script_tool_impl


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


def _write_scoped_skill_with_script(
    volume: Path,
    *,
    name: str,
    scope: str,
    description: str,
    script_name: str = "run.py",
    script_body: str = "print('ok')\n",
) -> Path:
    skill_dir = volume / "skills" / scope / name
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    script_path = scripts / script_name
    script_path.write_text(script_body, encoding="utf-8")
    return script_path


def _write_system_skill_with_script(volume: Path, name: str, script_name: str = "run.py") -> None:
    skill_dir = volume / "skills" / "system" / name
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "System skill"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    scripts.joinpath(script_name).write_text("print('ok')\n", encoding="utf-8")


def _run_script_context(
    volume: Path,
    *,
    selected: list[str],
    excluded: list[str] | None = None,
) -> SkillRuntimeContext:
    from fleet_rlm.skills.schemas import SkillVisibilityPolicy

    return SkillRuntimeContext(
        volume_mount_path=str(volume),
        selected_skill_ids=selected,
        visibility=SkillVisibilityPolicy(excluded_skill_ids=excluded or []),
    )


def _fake_interpreter(volume: Path) -> SimpleNamespace:
    return SimpleNamespace(
        volume_mount_path=str(volume),
        delegate_result_truncation_chars=8000,
        execute=lambda code, variables=None: SimpleNamespace(
            output={
                "success": True,
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }
        ),
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


def test_validate_skill_script_request_rejects_non_selected_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_system_skill_with_script(volume, "alpha")
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        selected_skill_ids=[],
    )
    with pytest.raises(SkillNotFoundError):
        validate_skill_script_request("alpha", "scripts/run.py", context=context)


def test_run_skill_script_rejects_non_selected_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=[]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Skill script not found or inaccessible."


def test_run_skill_script_rejects_hidden_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"], excluded=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Skill script not found or inaccessible."
    assert "alpha" not in str(payload)


def test_run_skill_script_rejects_absolute_script_path(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "/etc/passwd",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Invalid skill script path."


def test_run_skill_script_rejects_traversal_path(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/../SKILL.md",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Invalid skill script path."


def test_run_skill_script_rejects_backslash_traversal(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts\\run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Invalid skill script path."


def test_run_skill_script_rejects_script_outside_scripts_dir(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "references/note.md",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Invalid skill script path."


def test_run_skill_script_rejects_script_not_in_inventory(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/missing.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Skill script not found or inaccessible."


def test_run_skill_script_rejects_untrusted_user_authored_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="user", description="User skill")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is False
    assert payload["error"] == "Skill script execution is not permitted."


def test_run_skill_script_allows_builtin_selected_script(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is True
    assert payload["exit_code"] == 0
    assert payload["stdout"] == "ok"


def test_run_skill_script_allows_admin_approved_selected_script(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="org", description="Org skill")

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is True
    assert payload["exit_code"] == 0


def test_run_skill_script_invokes_daytona_execution_helper_not_host_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")
    captured: dict[str, object] = {}

    def fake_execute_sandbox_tool(interpreter, code, variables=None):
        captured["code"] = code
        captured["variables"] = variables
        return {"success": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("host subprocess forbidden"))
    monkeypatch.setattr(
        "fleet_rlm.skills.script_execution.execute_sandbox_tool",
        fake_execute_sandbox_tool,
    )

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=_fake_interpreter(volume),
    )

    assert payload["success"] is True
    assert "subprocess.run" in str(captured["code"])
    assert captured["variables"]["_script_path"].endswith("skills/system/alpha/scripts/run.py")


def test_run_skill_script_returns_sanitized_public_error_payload(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_scoped_skill_with_script(volume, name="alpha", scope="system", description="Alpha")
    interpreter = SimpleNamespace(
        volume_mount_path=str(volume),
        delegate_result_truncation_chars=8000,
        execute=lambda code, variables=None: SimpleNamespace(
            output={
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "raw internal failure",
                "error": "Skill script execution failed.",
            }
        ),
    )

    payload = run_skill_script_tool_impl(
        "alpha",
        "scripts/run.py",
        context=_run_script_context(volume, selected=["alpha"]),
        interpreter=interpreter,
    )

    assert payload["success"] is False
    assert payload["error"] == "Skill script execution failed."
    assert payload.get("stderr") is None
    assert "raw internal failure" not in str(payload)
