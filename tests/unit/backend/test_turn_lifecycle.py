"""Turn lifecycle settlement across its caller-facing seam."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_success_validates_and_publishes_before_atomic_commit() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.turn_lifecycle import (
        CommittedTurnReceipt,
        ExecuteTurn,
        TurnLifecycleModule,
        _TurnClaimToken,
    )
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    data = b"{}"
    access = TurnAccess(uuid4(), uuid4())
    run_id, session_id, artifact_id = uuid4(), uuid4(), uuid4()
    candidate = ArtifactCandidate(
        artifact_id,
        access.user_id,
        access.workspace_id,
        session_id,
        run_id,
        "json",
        "result",
        "application/json",
        len(data),
        sha256(data).hexdigest(),
        "/staging/result.json",
        "/artifacts/result.json",
    )

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )

    class Store:
        committed = None

        async def commit(self, claimed, committed, artifacts):
            self.committed = committed
            assert claimed is turn
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

        async def fail(self, claimed, failure):
            raise AssertionError((claimed, failure))

    class Sink:
        values = {candidate.staging_path: data}
        operations: list[tuple[str, str]] = []

        async def read(self, location, *, max_bytes):
            assert max_bytes >= len(data)
            self.operations.append(("read", location))
            return self.values[location]

        async def write(self, location, value):
            self.operations.append(("write", location))
            self.values[location] = value

        async def remove(self, location):
            self.operations.append(("remove", location))
            self.values.pop(location, None)

    store, sink = Store(), Sink()
    receipt = await TurnLifecycleModule(store, max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(terminal_status="completed", text="done", artifact_candidates=(candidate,)),
        artifact_sink=sink,
    )

    assert receipt.committed_turn.text == "done"
    assert sink.operations == [
        ("read", candidate.staging_path),
        ("write", candidate.durable_path),
        ("remove", candidate.staging_path),
    ]


@pytest.mark.asyncio
async def test_integrity_failure_does_not_publish_and_finalizes_safely() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, FailedRunReceipt, TurnLifecycleModule, _TurnClaimToken
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session_id,
        run_id,
        "text",
        None,
        "text/plain",
        3,
        sha256(b"abc").hexdigest(),
        "/s/a",
        "/d/a",
    )

    class Store:
        async def fail(self, claimed, failure):
            return FailedRunReceipt(claimed.run_id, failure.terminal_status, failure.public_message, True)

        async def commit(self, *args):
            raise AssertionError(args)

    class Sink:
        async def read(self, location, *, max_bytes):
            return b"bad-value"

        async def write(self, location, value):
            raise AssertionError((location, value))

        async def remove(self, location):
            return None

    receipt = await TurnLifecycleModule(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(terminal_status="completed", artifact_candidates=(candidate,)),
        artifact_sink=Sink(),
    )

    assert receipt.public_message == "Turn could not be committed"
