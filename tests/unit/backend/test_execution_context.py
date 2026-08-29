"""Ready-to-run immutable RLM context contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_execution_context_is_immutable_and_contains_prepared_runner_inputs() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
    from fleet_rlm.rlm.runtime import (
        ExecutionRuntime,
        RLMExecutionContext,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.sessions.models import TurnAccess

    session_id = uuid4()
    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=session_id, access=TurnAccess(user_id=uuid4(), workspace_id=uuid4())
        ),
        session=SessionView(
            request="inspect",
            session_context=SessionContextManifest(
                session_id,
                3,
                1,
                (TurnPreview(1, "user", "prior"),),
            ),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
            options=RLMOptions(),
            deadline=123.0,
            interpreter=SimpleNamespace(execute=lambda code: code),
            cancellation_requested=lambda: False,
        ),
        capabilities=SimpleNamespace(),
    )

    assert context.identity.session_id == session_id
    assert context.session.request == "inspect"
    assert context.execution.deadline == 123.0
    assert context.session.attachments == ()
    assert not hasattr(context, "settings")
    assert not hasattr(context, "turn_store")
    with pytest.raises(FrozenInstanceError):
        context.session.request = "changed"  # type: ignore[misc]
