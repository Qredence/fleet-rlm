from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dspy
import pytest

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.stream_turn import _build_stream_kwargs
from fleet_rlm.files.schemas import AttachedFiles, AttachmentRef
from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule
from fleet_rlm.skills.active import ActiveSkills
from fleet_rlm.skills.loader import clear_skill_cache, load_skill_impl
from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy
from fleet_rlm.tools.skill_tools import load_skill


def _write_skill_md(path: Path, *, name: str, description: str) -> None:
    path.write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def _write_directory_skill(skills_root: Path, scope: str, name: str, description: str) -> None:
    skill_dir = skills_root / scope / name
    skill_dir.mkdir(parents=True)
    _write_skill_md(skill_dir / "SKILL.md", name=name, description=description)
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "note.md").write_text("reference body\n", encoding="utf-8")


def test_build_stream_kwargs_forwards_selected_skill_ids() -> None:
    from fleet_rlm.api.auth.types import NormalizedIdentity
    from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime

    prepared = PreparedChatRuntime(
        cfg=object(),
        planner_lm=object(),
        delegate_lm=object(),
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )
    ctx = ChatExecutionContext(
        prepared=prepared,
        identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
        session_id=None,
        canonical_workspace_id="w",
        canonical_user_id="u",
        owner_tenant_claim="t",
        owner_user_claim="u",
        cancel_flag={"cancelled": False},
        controls=TurnControls(selected_skill_ids=["diagnostics", "rlm"]),
    )
    kwargs = _build_stream_kwargs(ctx, "hello")
    assert kwargs["selected_skill_ids"] == ["diagnostics", "rlm"]


def test_escalation_call_args_include_selected_skill_ids() -> None:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._use_escalation = True
    runtime.core_memory = {}
    runtime.history = dspy.History(messages=[])
    runtime.execution_mode = "auto"
    runtime.conversation_summary = ""
    runtime._turn_context = None
    runtime._selected_skill_ids = ["diagnostics"]

    args = runtime._escalation_call_args("hello")

    assert args["selected_skill_ids"] == ["diagnostics"]


def test_build_stream_kwargs_forwards_attached_files() -> None:
    from fleet_rlm.api.auth.types import NormalizedIdentity
    from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime

    attached = AttachedFiles(attachments=[AttachmentRef(id="a" * 32, filename="notes.txt", size_bytes=1)])
    prepared = PreparedChatRuntime(
        cfg=object(),
        planner_lm=object(),
        delegate_lm=object(),
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )
    ctx = ChatExecutionContext(
        prepared=prepared,
        identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
        session_id="sess-1",
        canonical_workspace_id="w",
        canonical_user_id="u",
        owner_tenant_claim="t",
        owner_user_claim="u",
        cancel_flag={"cancelled": False},
        controls=TurnControls(attached_files=attached),
    )

    kwargs = _build_stream_kwargs(ctx, "hello")

    assert kwargs["attached_files"] is attached


def test_escalation_call_args_include_attached_files() -> None:
    attached = AttachedFiles(attachments=[AttachmentRef(id="b" * 32, filename="data.csv", size_bytes=2)])
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._use_escalation = True
    runtime.core_memory = {}
    runtime.history = dspy.History(messages=[])
    runtime.execution_mode = "auto"
    runtime.conversation_summary = ""
    runtime._turn_context = None
    runtime._selected_skill_ids = []
    runtime._attached_files = attached

    args = runtime._escalation_call_args("hello")

    assert args["attached_files"] is attached


def test_legacy_enrich_with_skills_honors_explicit_selected_skill_ids(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    skills_root = volume / "skills" / "user"
    skills_root.mkdir(parents=True)
    _write_skill_md(
        skills_root / "my-flat.md",
        name="my-flat",
        description="Flat legacy skill.",
    )
    interpreter = SimpleNamespace(volume_mount_path=str(volume))
    module = EscalatingFleetModule(interpreter=interpreter, tools=[])

    _, selected, active_skills = module._enrich_with_skills(
        "hello",
        "",
        selected_skill_ids=["my-flat"],
    )

    assert selected == ["my-flat"]
    assert active_skills.selected == ["my-flat"]
    assert active_skills.instructions["my-flat"].startswith("---")


def test_legacy_enrich_with_skills_loads_directory_style_volume_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume / "skills", "user", "dir-skill", "Directory skill.")
    interpreter = SimpleNamespace(volume_mount_path=str(volume))
    module = EscalatingFleetModule(interpreter=interpreter, tools=[])

    _, selected, active_skills = module._enrich_with_skills(
        "hello",
        "",
        selected_skill_ids=["dir-skill"],
    )

    assert selected == ["dir-skill"]
    assert active_skills.resources.get("dir-skill")
    assert active_skills.sandbox_paths.get("dir-skill", "").endswith("/skills/user/dir-skill")


def test_legacy_enrich_with_skills_auto_discovers_directory_style_volume_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume / "skills", "user", "alpha-route", "Zephyr alpha routing support.")
    interpreter = SimpleNamespace(volume_mount_path=str(volume))
    module = EscalatingFleetModule(interpreter=interpreter, tools=[])

    _, selected, active_skills = module._enrich_with_skills(
        "Use zephyr alpha routing",
        "",
    )

    assert selected == ["alpha-route"]
    assert active_skills.selected == ["alpha-route"]
    assert active_skills.resources.get("alpha-route")
    assert active_skills.sandbox_paths.get("alpha-route", "").endswith("/skills/user/alpha-route")


def test_legacy_enrich_with_skills_drops_invisible_explicit_skill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    interpreter = SimpleNamespace(volume_mount_path=None)
    module = EscalatingFleetModule(interpreter=interpreter, tools=[])

    with caplog.at_level(logging.WARNING):
        _, selected, active_skills = module._enrich_with_skills(
            "hello",
            "",
            selected_skill_ids=["nonexistent-invisible-skill"],
        )

    assert selected == []
    assert active_skills.selected == []
    assert any("nonexistent-invisible-skill" in record.message for record in caplog.records)


def test_load_skill_impl_rejects_invisible_skill() -> None:
    clear_skill_cache()
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["diagnostics"]),
    )
    result = load_skill_impl("diagnostics", context=context)
    assert result.status == "error"
    assert "not visible" in (result.error or "").lower()


def test_load_skill_tool_rejects_invisible_skill() -> None:
    clear_skill_cache()
    with patch(
        "fleet_rlm.tools.skill_tools.default_skill_runtime_context",
        return_value=SkillRuntimeContext(
            visibility=SkillVisibilityPolicy(excluded_skill_ids=["diagnostics"]),
        ),
    ):
        payload = load_skill("diagnostics")
    assert payload["status"] == "not_found"
    assert payload["error"] == "Skill not found or inaccessible."
    assert "diagnostics" not in str(payload)


def test_escalating_forward_passes_selected_skill_ids_to_prepare_turn() -> None:
    module = EscalatingFleetModule(interpreter=None, tools=[])
    module._prepare_turn = MagicMock(
        return_value=SimpleNamespace(
            should_route_rlm=False,
            routing_decision=None,
            source_url=None,
            core_memory="",
            selected_skills=[],
            active_skills=ActiveSkills(),
            history=dspy.History(messages=[]),
        )
    )
    module._route_turn = MagicMock(return_value="direct")
    module.respond = MagicMock(return_value=dspy.Prediction(response="ok"))

    module.forward(
        user_request="hello",
        selected_skill_ids=["diagnostics"],
    )

    module._prepare_turn.assert_called_once()
    assert module._prepare_turn.call_args.kwargs["selected_skill_ids"] == ["diagnostics"]
