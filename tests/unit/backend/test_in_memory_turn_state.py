"""Hermetic Turn-state parity adapter behavior."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_begin_commit_replay_and_history_are_input_bound() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn, ReplayTurn
    from fleet_rlm.persistence.repositories.turns import InMemoryTurnStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryTurnStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    request = BeginTurn(access, session_id, TurnInput("hello"), "key", run_id)

    begun = await store.begin(request)
    assert isinstance(begun, ExecuteTurn)
    committed = CommittedTurn(
        1,
        (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("world")),
    )
    await store.commit(begun, committed, ())
    replay = await store.begin(request)

    assert isinstance(replay, ReplayTurn)
    assert replay.committed_turn.text == "world"
    next_turn = await store.begin(BeginTurn(access, session_id, TurnInput("next"), "next", uuid4()))
    assert isinstance(next_turn, ExecuteTurn)
    assert [(item.role, item.content) for item in next_turn.history.messages] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


@pytest.mark.asyncio
async def test_idempotency_mismatch_single_active_and_cancellation() -> None:
    from fleet_rlm.chat.turn_lifecycle import (
        BeginTurn,
        TurnIdempotencyMismatchError,
        TurnInProgressError,
    )
    from fleet_rlm.persistence.repositories.turns import InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryTurnStateStore()
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()
    await store.add_session(session_id, access)
    begun = await store.begin(BeginTurn(access, session_id, TurnInput("hello"), "key", run_id))

    with pytest.raises(TurnIdempotencyMismatchError):
        await store.begin(BeginTurn(access, session_id, TurnInput("different"), "key", uuid4()))
    with pytest.raises(TurnInProgressError):
        await store.begin(BeginTurn(access, session_id, TurnInput("other"), "other", uuid4()))
    assert await store.request_cancel(access, run_id) == "requested"
    assert await store.request_cancel(access, run_id) == "already_requested"
    assert await begun.cancellation_requested()


@pytest.mark.asyncio
async def test_idempotency_claim_changes_when_skill_selections_change() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnIdempotencyMismatchError
    from fleet_rlm.persistence.repositories.turns import InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.skills.models import SkillSelectionRef

    store = InMemoryTurnStateStore()
    access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
    skill_id = uuid4()
    await store.add_session(session_id, access)
    await store.begin(
        BeginTurn(
            access,
            session_id,
            TurnInput("hello", skill_selections=(SkillSelectionRef(skill_id, "1.0.0"),)),
            "skill-key",
            uuid4(),
        )
    )

    with pytest.raises(TurnIdempotencyMismatchError):
        await store.begin(
            BeginTurn(
                access,
                session_id,
                TurnInput("hello", skill_selections=(SkillSelectionRef(skill_id, "2.0.0"),)),
                "skill-key",
                uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_archived_session_cannot_begin_turn() -> None:
    from fleet_rlm.chat.turn_lifecycle import BeginTurn, TurnNotFoundError
    from fleet_rlm.persistence.repositories import InMemorySessionCatalog, InMemoryTurnStateStore
    from fleet_rlm.sessions.models import TurnAccess, TurnInput

    store = InMemoryTurnStateStore()
    catalog = InMemorySessionCatalog(store)
    access = TurnAccess(uuid4(), uuid4())
    session = await catalog.create(user_id=access.user_id, workspace_id=access.workspace_id, title="archived")
    await catalog.update(
        session.id,
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        title=None,
        status="archived",
    )

    with pytest.raises(TurnNotFoundError):
        await store.begin(BeginTurn(access, session.id, TurnInput("hello"), "key", uuid4()))
