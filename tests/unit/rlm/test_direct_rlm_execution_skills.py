from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dspy

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.rlm.execution import run_direct_rlm_turn


def test_run_direct_rlm_turn_uses_visibility_validated_explicit_skills(sample_context: ChatExecutionContext) -> None:
    ctx = replace(
        sample_context,
        controls=TurnControls(
            execution_backend=ExecutionBackend.direct_rlm,
            selected_skill_ids=["diagnostics"],
        ),
    )
    interpreter = SimpleNamespace(volume_mount_path=None, sub_lm=None)
    agent_runtime = SimpleNamespace(core_memory="", history=dspy.History(messages=[]))

    fake_rlm = MagicMock(return_value=dspy.Prediction(response="ok"))

    with (
        patch("fleet_rlm.rlm.execution.create_runtime_rlm", return_value=fake_rlm),
        patch("fleet_rlm.rlm.execution.interpreter_delegation_tools", return_value=[]),
    ):
        run_direct_rlm_turn(
            ctx=ctx,
            message="debug this",
            interpreter=interpreter,
            agent_runtime=agent_runtime,
        )

    active_skills = fake_rlm.call_args.kwargs["active_skills"]
    assert active_skills.selected == ["diagnostics"]
    assert active_skills.instructions["diagnostics"].startswith("---")


def test_run_direct_rlm_turn_drops_invisible_explicit_skill(sample_context: ChatExecutionContext) -> None:
    ctx = replace(
        sample_context,
        controls=TurnControls(
            execution_backend=ExecutionBackend.direct_rlm,
            selected_skill_ids=["nonexistent-invisible-skill"],
        ),
    )
    interpreter = SimpleNamespace(volume_mount_path=None, sub_lm=None)
    agent_runtime = SimpleNamespace(core_memory="", history=dspy.History(messages=[]))

    fake_rlm = MagicMock(return_value=dspy.Prediction(response="ok"))

    with (
        patch("fleet_rlm.rlm.execution.create_runtime_rlm", return_value=fake_rlm),
        patch("fleet_rlm.rlm.execution.interpreter_delegation_tools", return_value=[]),
    ):
        run_direct_rlm_turn(
            ctx=ctx,
            message="hello",
            interpreter=interpreter,
            agent_runtime=agent_runtime,
        )

    active_skills = fake_rlm.call_args.kwargs["active_skills"]
    assert active_skills.selected == []
