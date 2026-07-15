"""Ready-to-run immutable RLM context contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_execution_context_has_only_prepared_runner_inputs() -> None:
    from fleet_rlm.rlm.context import RLMExecutionContext, RLMHistoryMessage
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import TurnAccess

    context = RLMExecutionContext(
        run_id=uuid4(),
        session_id=uuid4(),
        access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
        request="inspect",
        history=(RLMHistoryMessage(role="user", content="prior"),),
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
        deadline=123.0,
        interpreter=SimpleNamespace(execute=lambda code: code),
        attachments=(),
        capabilities=SimpleNamespace(),
        cancellation_requested=lambda: False,
        preparation_notices=(),
    )

    assert {item.name for item in fields(context)} == {
        "run_id",
        "session_id",
        "access",
        "request",
        "history",
        "models",
        "options",
        "deadline",
        "interpreter",
        "attachments",
        "capabilities",
        "cancellation_requested",
        "preparation_notices",
    }
    with pytest.raises(FrozenInstanceError):
        context.request = "changed"  # type: ignore[misc]
