"""Private RLM observation-session contracts."""

from __future__ import annotations

from uuid import uuid4

import pytest

from fleet_rlm.rlm.events import RLMOutput, RunStarted, Status
from fleet_rlm.rlm.observation import ObservationSession


@pytest.mark.asyncio
async def test_observation_session_separates_stream_envelopes_from_execution_details() -> None:
    session = ObservationSession(uuid4(), uuid4())

    started = session.record_event(RunStarted(delivery="live"))
    status = session.record_event(Status("execution", "running"))
    detail = session.record(RLMOutput("answer", 1))

    assert [started.sequence, status.sequence, detail.sequence] == [1, 2, 3]
    assert session.details == [RLMOutput("answer", 1)]
