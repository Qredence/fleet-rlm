"""Ready-to-run immutable RLM context contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_execution_context_is_immutable_and_contains_prepared_runner_inputs() -> None:
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.rlm.context import RLMExecutionContext
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import TurnAccess

    session_id = uuid4()
    context = RLMExecutionContext(
        run_id=uuid4(),
        session_id=session_id,
        access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
        request="inspect",
        session_context=SessionContextManifest(
            session_id,
            3,
            1,
            (TurnPreview(1, "user", "prior"),),
        ),
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        deadline=123.0,
        interpreter=SimpleNamespace(execute=lambda code: code),
        attachments=(),
        capabilities=SimpleNamespace(),
        cancellation_requested=lambda: False,
        preparation_notices=(),
    )

    assert context.session_id == session_id
    assert context.request == "inspect"
    assert context.deadline == 123.0
    assert context.attachments == ()
    assert not hasattr(context, "settings")
    assert not hasattr(context, "turn_store")
    with pytest.raises(FrozenInstanceError):
        context.request = "changed"  # type: ignore[misc]
