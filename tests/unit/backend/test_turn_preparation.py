"""Prepare-before-stream resource ownership and cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_preparation_bounds_history_and_closes_in_dependency_order() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import DefaultTurnPreparer, RunEnvironment
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.context import RLMExecutionSpec
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput

    operations: list[str] = []

    class Sink:
        async def read(self, location, *, max_bytes):
            del location, max_bytes
            return b""

        async def write(self, location, data):
            del location, data
            return None

        async def remove(self, location):
            del location
            operations.append("remove-artifact")

        async def read_private(self, location):
            del location
            return b""

        async def write_private(self, location, data):
            del location, data
            return None

        async def remove_private(self, location):
            del location
            operations.append("remove-attachment")

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class Capabilities:
        spec = RLMExecutionSpec()

        def drain_public_details(self):
            return ()

        def drain_artifact_candidates(self):
            return ()

        async def aclose(self):
            operations.append("close-capabilities")

    sink = Sink()

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn
            assert deadline > 0

            async def release():
                operations.append("release-environment")

            return RunEnvironment(SimpleNamespace(), sink, sink, release)

    class CapabilityFactory:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments
            assert deadline > 0
            return Capabilities()

    async def not_cancelled():
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("next"),
        SessionHistory((HistoryMessage("user", "prior"),)),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    prepared = await DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=CapabilityFactory(),
    ).prepare(turn, deadline=float("inf"))

    assert prepared.execution.session_context.to_input() == {
        "session_id": str(turn.session_id),
        "checkpoint_version": 0,
        "message_count": 1,
        "recent": [{"ordinal": 1, "role": "user", "preview": "prior"}],
    }
    assert prepared.result_snapshot_sink is None
    await prepared.aclose()
    await prepared.aclose()
    assert operations == ["close-capabilities", "release-environment"]


@pytest.mark.asyncio
async def test_capability_preparation_is_bounded_by_turn_deadline_and_releases_environment() -> None:
    import asyncio

    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import DefaultTurnPreparer, RunEnvironment, TurnPreparationTimeoutError
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    released = False

    class Sink:
        async def remove_private(self, location):
            del location
            return None

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                nonlocal released
                released = True

            sink = Sink()
            return RunEnvironment(None, sink, sink, release)

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments((), ())

    class SlowCapabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            await asyncio.sleep(60)
            raise AssertionError("deadline did not cancel capability preparation")

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(()),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    module = DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=SlowCapabilities(),
    )

    with pytest.raises(TurnPreparationTimeoutError, match="timed out"):
        await module.prepare(turn, deadline=asyncio.get_running_loop().time() + 0.01)
    assert released is True


@pytest.mark.asyncio
async def test_preparation_failure_removes_staged_run_bytes_but_not_session_workspace() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import DefaultTurnPreparer, RunEnvironment
    from fleet_rlm.files.models import AttachmentRef, PreparedAttachments, StagedAttachment
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access, run_id, session_id, attachment_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4(), uuid4()
    staged_path = f"/sessions/{session_id}/runs/{run_id}/attachments/{attachment_id}.txt"
    workspace_path = f"/sessions/{session_id}/workspace/notes.txt"
    values = {staged_path: b"uploaded input", workspace_path: b"immediate workspace state"}

    class Sink:
        async def remove_private(self, location):
            values.pop(location, None)

    class Environments:
        async def acquire(self, turn, *, deadline):
            del turn, deadline

            async def release():
                return None

            sink = Sink()
            return RunEnvironment(None, sink, sink, release)

    class Attachments:
        async def prepare_run(self, access, ids, run, sink):
            del access, ids, run, sink
            return PreparedAttachments(
                (
                    AttachmentRef(
                        attachment_id,
                        "input.txt",
                        "text/plain",
                        len(values[staged_path]),
                        "0" * 64,
                    ),
                ),
                (StagedAttachment(attachment_id, staged_path),),
            )

    class FailingCapabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            raise RuntimeError("private capability failure")

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        run_id,
        session_id,
        access,
        TurnInput("prepare", (attachment_id,)),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    module = DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=FailingCapabilities(),
    )

    with pytest.raises(RuntimeError, match="private capability failure"):
        await module.prepare(turn, deadline=float("inf"))

    assert staged_path not in values
    assert values == {workspace_path: b"immediate workspace state"}
