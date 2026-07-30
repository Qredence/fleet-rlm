"""Database failures during Turn preparation become safe typed failures."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_connection_reset_during_capability_preparation_is_unavailable() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import (
        DefaultTurnPreparer,
        RunEnvironment,
        TurnPreparationUnavailableError,
    )
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    class Sink:
        async def remove_private(self, location):
            del location

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
            return PreparedAttachments((), ())

    class Capabilities:
        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            raise DatabaseConnectionError("connection reset during TLS handshake")

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    preparer = DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=Capabilities(),
    )

    with pytest.raises(TurnPreparationUnavailableError, match="capabilities"):
        await preparer.prepare(turn, deadline=float("inf"))


@pytest.mark.asyncio
async def test_connection_reset_during_attachment_staging_is_unavailable() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import (
        DefaultTurnPreparer,
        RunEnvironment,
        TurnPreparationUnavailableError,
    )
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    class Sink:
        async def remove_private(self, location):
            del location

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
            raise DatabaseConnectionError("attachment catalog unavailable")

    async def not_cancelled() -> bool:
        return False

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        not_cancelled,
        _TurnClaimToken(uuid4()),
    )
    preparer = DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=object(),
    )

    with pytest.raises(TurnPreparationUnavailableError, match="attachments"):
        await preparer.prepare(turn, deadline=float("inf"))


@pytest.mark.asyncio
async def test_connection_reset_during_post_capability_cancellation_probe_is_unavailable() -> None:
    from fleet_rlm.chat.turn_lifecycle import ExecuteTurn, _TurnClaimToken
    from fleet_rlm.chat.turn_preparation import (
        DefaultTurnPreparer,
        RunEnvironment,
        TurnPreparationUnavailableError,
    )
    from fleet_rlm.files.models import PreparedAttachments
    from fleet_rlm.persistence.database import DatabaseConnectionError
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    class Sink:
        async def remove_private(self, location):
            del location

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
            return PreparedAttachments((), ())

    class Capabilities:
        preparation_notices = ()

        async def prepare(self, turn, environment, attachments, *, deadline):
            del turn, environment, attachments, deadline
            return self

        async def aclose(self):
            return None

    cancellation_checks = 0

    async def cancellation_probe() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        if cancellation_checks <= 2:
            return False
        raise DatabaseConnectionError("cancellation probe unavailable")

    turn = ExecuteTurn(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("prepare"),
        SessionHistory(),
        cancellation_probe,
        _TurnClaimToken(uuid4()),
    )
    preparer = DefaultTurnPreparer(
        models=RLMModelBundle(object(), object()),
        options=RLMOptions(),
        attachments=Attachments(),
        environments=Environments(),
        capabilities=Capabilities(),
    )

    with pytest.raises(TurnPreparationUnavailableError, match="cancellation"):
        await preparer.prepare(turn, deadline=float("inf"))
