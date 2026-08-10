"""Turn lifecycle settlement across its caller-facing seam."""

from __future__ import annotations

from hashlib import sha256
from typing import ClassVar
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_success_validates_and_publishes_before_atomic_commit() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
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

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        committed = None

        async def commit(self, claimed, committed, artifacts):
            self.committed = committed
            assert claimed is turn
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    class Sink:
        values: ClassVar[dict[object, object]] = {candidate.staging_path: data}
        operations: ClassVar[list[tuple[str, str]]] = []

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
    receipt = await RunLifecycleService(store, max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            artifact_candidates=(candidate,),
        ),
        artifact_sink=sink,
    )

    assert receipt.committed_turn.text == "done"
    assert sink.operations == [
        ("read", candidate.staging_path),
        ("write", candidate.durable_path),
        ("remove", candidate.staging_path),
    ]


@pytest.mark.asyncio
async def test_authority_revocation_after_artifact_publish_rolls_back_before_commit() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    data = b"artifact"
    access = TurnAccess(uuid4(), uuid4())
    run_id, session_id = uuid4(), uuid4()
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session_id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/staging/result.txt",
        "/artifacts/result.txt",
    )

    async def not_cancelled() -> bool:
        """
        Determine whether cancellation has occurred.

        Returns:
                bool: `False`, indicating that cancellation has occurred.
        """
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        commits = 0

        async def commit(self, *_args):
            self.commits += 1
            raise AssertionError("revoked Turn must not reach commit")

        async def transition_claim(self, claimed, command):
            """
            Create a failed-run receipt from the claimed run and command failure.

            Parameters:
                claimed: The claimed run whose identifier is included in the receipt.
                command: The command containing the failure code and public message.

            Returns:
                FailedRunReceipt: A receipt representing the failed run.
            """
            from fleet_rlm.chat.run_lifecycle import FailedRunReceipt

            return FailedRunReceipt(
                claimed.run_id,
                "failed",
                command.failure.code,
                command.failure.public_message,
                True,
            )

    class Sink:
        values: ClassVar[dict[str, bytes]] = {candidate.staging_path: data}

        async def read(self, location, *, max_bytes):
            """Read the value stored at the specified location."""
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            """
            Store a value at the specified location and revoke turn authority.
            """
            self.values[location] = value
            turn.authority.revoke()

        async def remove(self, location):
            """
            Remove the value associated with a location.

            Parameters:
                location: The location whose value should be removed.
            """
            self.values.pop(location, None)

    store, sink = Store(), Sink()
    receipt = await RunLifecycleService(store, max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            artifact_candidates=(candidate,),
        ),
        artifact_sink=sink,
    )

    assert isinstance(receipt, FailedRunReceipt)
    assert store.commits == 0
    assert sink.values == {}


@pytest.mark.asyncio
async def test_integrity_failure_does_not_publish_and_finalizes_safely() -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, FailedRunReceipt, RunLifecycleService, _RunClaimToken
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
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
        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

        async def commit(self, *args):
            raise AssertionError(args)

    class Sink:
        async def read(self, location, *, max_bytes):
            del location, max_bytes
            return b"bad-value"

        async def write(self, location, value):
            raise AssertionError((location, value))

        async def remove(self, location):
            del location
            return None

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(
            terminal_status="completed",
            prediction=PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            artifact_candidates=(candidate,),
        ),
        artifact_sink=Sink(),
    )

    assert receipt.public_message == "Turn could not be committed"


@pytest.mark.asyncio
async def test_daytona_success_writes_snapshot_before_commit_and_retains_it() -> None:
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        CommittedTurnReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    operations: list[str] = []

    class Store:
        async def commit(self, claimed, committed, artifacts):
            assert claimed is turn
            assert snapshot.values.keys() == {snapshot.path}
            operations.append("commit")
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    class SnapshotSink:
        path = f"/sessions/{session_id}/runs/{run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, requested_session_id, requested_run_id):
            assert (requested_session_id, requested_run_id) == (session_id, run_id)
            return self.path

        async def write(self, location, data):
            operations.append("snapshot.write")
            self.values[location] = data

        async def remove(self, location):
            operations.append("snapshot.remove")
            self.values.pop(location, None)

    snapshot = SnapshotSink()
    receipt = await RunLifecycleService(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage={"iterations": 2, "observed_lm_usage": {}, "duration_ms": 3},
        ),
        result_snapshot_sink=snapshot,
    )

    assert isinstance(receipt, CommittedTurnReceipt)
    assert operations == ["snapshot.write", "commit"]
    assert snapshot.values.keys() == {snapshot.path}


@pytest.mark.asyncio
async def test_commit_failure_removes_snapshot_logs_stage_and_keeps_public_failure_opaque(caplog) -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    data = b"artifact"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session_id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/staging/a",
        "/artifacts/a",
    )
    operations: list[str] = []

    class Store:
        async def commit(self, claimed, committed, artifacts):
            del claimed, committed, artifacts
            operations.append("commit")
            raise RuntimeError("database unavailable")

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            operations.append("fail")
            return FailedRunReceipt(
                claimed.run_id,
                "failed",
                failure.failure_code,
                failure.public_message,
                True,
            )

    class ArtifactSink:
        values: ClassVar[dict[object, object]] = {candidate.staging_path: data}

        async def read(self, location, *, max_bytes):
            del max_bytes
            return self.values[location]

        async def write(self, location, value):
            operations.append(f"artifact.write:{location}")
            self.values[location] = value

        async def remove(self, location):
            operations.append(f"artifact.remove:{location}")
            self.values.pop(location, None)

    class SnapshotSink:
        path = f"/sessions/{session_id}/runs/{run_id}/result.json"
        values: ClassVar[dict[str, bytes]] = {}

        def result_path(self, requested_session_id, requested_run_id):
            del requested_session_id, requested_run_id
            return self.path

        async def write(self, location, value):
            operations.append(f"snapshot.write:{location}")
            self.values[location] = value

        async def remove(self, location):
            operations.append(f"snapshot.remove:{location}")
            self.values.pop(location, None)

    artifacts, snapshot = ArtifactSink(), SnapshotSink()
    receipt = await RunLifecycleService(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
            artifact_candidates=(candidate,),
        ),
        artifact_sink=artifacts,
        result_snapshot_sink=snapshot,
    )

    assert isinstance(receipt, FailedRunReceipt)
    assert receipt.failure_code == "commit_failed"
    assert receipt.public_message == "Turn could not be committed"
    record = next(record for record in caplog.records if record.message.startswith("Turn finalization failed"))
    assert record.levelname == "ERROR"
    assert "stage=commit_turn" in record.message
    assert f"session_id={session_id}" in record.message
    assert f"run_id={run_id}" in record.message
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert "database unavailable" not in receipt.public_message
    assert candidate.durable_path not in artifacts.values
    assert snapshot.values == {}
    assert operations == [
        f"artifact.write:{candidate.durable_path}",
        f"snapshot.write:{snapshot.path}",
        "commit",
        f"snapshot.remove:{snapshot.path}",
        f"artifact.remove:{candidate.durable_path}",
        f"artifact.remove:{candidate.staging_path}",
        "fail",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "timeout"])
async def test_non_success_never_writes_result_snapshot(status: str) -> None:
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

    class NeverSnapshot:
        def __getattr__(self, name):
            raise AssertionError(name)

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(status, public_error_message="Turn failed"),
        result_snapshot_sink=NeverSnapshot(),
    )

    assert isinstance(receipt, FailedRunReceipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "timeout"])
async def test_non_success_removes_run_local_artifact_candidate_bytes(status: str) -> None:
    from fleet_rlm.artifacts.models import ArtifactCandidate
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    data = b"uncommitted"
    candidate = ArtifactCandidate(
        uuid4(),
        access.user_id,
        access.workspace_id,
        session_id,
        run_id,
        "text",
        None,
        "text/plain",
        len(data),
        sha256(data).hexdigest(),
        "/runs/current/artifact-candidate.txt",
        "/artifacts/never-published.txt",
    )

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

    class Sink:
        values: ClassVar[dict[object, object]] = {candidate.staging_path: data}
        removals: ClassVar[list[str]] = []

        async def remove(self, location):
            self.removals.append(location)
            self.values.pop(location, None)

    sink = Sink()
    receipt = await RunLifecycleService(Store(), max_artifact_bytes=100).finish(
        turn,
        RLMOutcome(status, artifact_candidates=(candidate,)),
        artifact_sink=sink,
    )

    assert isinstance(receipt, FailedRunReceipt)
    assert sink.values == {}
    assert sink.removals == [candidate.staging_path]


@pytest.mark.asyncio
async def test_memory_candidate_promotion_happens_after_atomic_commit_and_fails_soft() -> None:

    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedTurnReceipt, RunLifecycleService, _RunClaimToken
    from fleet_rlm.files.memory_candidates import MemoryCandidate
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    run_id, session_id = uuid4(), uuid4()
    access = TurnAccess(uuid4(), uuid4())

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("promote this later"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )
    candidate = MemoryCandidate(
        candidate_id="cand00000001",
        category="Project",
        learning="settlement-visible learning",
        byte_size=len(b"settlement-visible learning"),
    )
    order: list[str] = []

    class Store:
        async def commit(self, claimed, committed, artifacts):
            del claimed
            order.append("commit")
            return CommittedTurnReceipt(run_id, 1, committed, artifacts)

    class BrokenPromotion:
        def __call__(self, candidates):
            assert candidates == (candidate,)
            order.append("promote")
            raise RuntimeError("promotion storage unavailable")

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=1024).finish(
        turn,
        RLMOutcome(
            "completed",
            prediction=PredictionResult("answer", {"answer": "done"}, "fleet.default", "1"),
            memory_candidates=(candidate,),
        ),
        memory_promotion=BrokenPromotion(),
    )

    assert order == ["commit", "promote"]
    assert receipt.checkpoint_version == 1
    # The turn remains durably committed; optional promotion failure is only a warning.
