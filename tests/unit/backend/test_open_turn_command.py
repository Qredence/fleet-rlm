"""Validated application command for Session-first Turn creation."""

from __future__ import annotations

from uuid import uuid4

import pytest


def test_open_turn_command_contains_only_claimed_canonical_values() -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    command = OpenTurnCommand(
        access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
        session_id=uuid4(),
        input=TurnInput(text="inspect"),
        idempotency_key="request-1",
        proposed_run_id=uuid4(),
    )

    assert command.idempotency_key == "request-1"
    assert command.input.text == "inspect"


@pytest.mark.parametrize("key", ["", "   ", "line\nbreak", "x" * 129])
def test_open_turn_command_rejects_invalid_idempotency_keys(key: str) -> None:
    from fleet_rlm.chat.commands import OpenTurnCommand
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    with pytest.raises(ValueError):
        OpenTurnCommand(
            access=TurnAccess(user_id=uuid4(), workspace_id=uuid4()),
            session_id=uuid4(),
            input=TurnInput(text="inspect"),
            idempotency_key=key,
            proposed_run_id=uuid4(),
        )
