"""P44 first-class durable Session History projection contract.

The tests exercise, in the exact order below:

1.  Failed, cancelled, timed-out, and uncommitted Turns are excluded.
2.  Committed user/assistant records are included.
3.  An empty committed-Turn list returns ``dspy.History(messages=[])``.
4.  Round-trip: every emitted ``dspy.History`` message has exactly the
    canonical ``{"request", "answer"}`` keys.
5.  ``validate_legacy_records`` tolerates canonical dicts and rejects
    non-canonical shapes.
6.  Cross-Session isolation: records from another Session are not present.
7.  Corrupt legacy payloads raise ``ValueError``.
8.  ``dspy.History`` reuse: identical input yields deep-equal messages.
9.  The returned object is exactly ``dspy.History`` (not a subclass or
    Pydantic shadow).
10. Store-level cross-Session isolation: a ``begin`` for one Session never
    loads another Session's committed records (in-memory and SQL stores).
11. A timed-out Turn persists only its durable terminal status — never a
    Session History tombstone — and stays out of the committed History
    projection (store → claim → ``claimed_history_records``).
12. Failed and cancelled Turns are excluded end-to-end through
    ``claimed_history_records``/``dspy_history_for_claim``.
13. ``claimed_history_records`` excludes tombstone-bearing checkpoints whose
    assistant messages carry failed/timed-out terminal metadata.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.events import observe_tool
from fleet_rlm.sessions.committed_turn import (
    CommittedTurn,
    StatusPart,
    TextPart,
    UsagePart,
)
from fleet_rlm.sessions.history import (
    SESSION_HISTORY_RESULT_BYTE_BUDGET,
    CommittedSessionHistory,
    SessionHistoryToolHost,
    committed_session_history_payload,
    to_canonical_history_records,
    to_dspy_history,
    validate_legacy_records,
)
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory
from tests.support.turn_settlement import TestingRunSettlement

_EMPTY_USAGE: dict[str, Any] = {
    "iterations": 0,
    "observed_lm_usage": {},
    "duration_ms": 0,
}


def _successful_turn(answer: str) -> CommittedTurn:
    """Build a successful committed Turn with one final text part."""
    return CommittedTurn(
        schema_version=1,
        parts=(
            UsagePart(value=dict(_EMPTY_USAGE)),
            TextPart(text=answer),
        ),
    )


def _terminal_turn(phase: str, status: str, text: str) -> CommittedTurn:
    """Build a terminal-failure committed Turn carrying a ``StatusPart``."""
    return CommittedTurn(
        schema_version=1,
        parts=(
            StatusPart(phase=phase, status=status),
            UsagePart(value=dict(_EMPTY_USAGE)),
            TextPart(text=text),
        ),
    )


def test_excludes_failed_cancelled_timed_out_and_uncommitted_turns() -> None:
    """Failed, cancelled, timed-out, and uncommitted Turns are excluded."""
    failed_turn = _terminal_turn("failed", "failed", "internal failure details")
    cancelled_turn = _terminal_turn("cancelled", "cancelled", "Turn cancelled")
    timed_out_turn = _terminal_turn("timed_out", "timed_out", "Turn timed out")
    successful_turn = _successful_turn("successful answer")
    # An uncommitted Turn is, by definition, not a ``CommittedTurn``; the
    # caller has simply not included it in the input sequence.
    uncommitted_turn: CommittedTurn | None = None

    committed_turns: Sequence[CommittedTurn] = (
        failed_turn,
        cancelled_turn,
        timed_out_turn,
        successful_turn,
    )
    user_requests: Sequence[str] = (
        "failed request",
        "cancelled request",
        "timed_out request",
        "successful request",
    )
    assert uncommitted_turn is None  # not in the input sequence

    records = to_canonical_history_records(committed_turns, user_requests=user_requests)

    assert records == [
        {"request": "successful request", "answer": "successful answer"},
    ]


def test_includes_committed_user_assistant_records_only() -> None:
    """Successful committed Turns are emitted as canonical records in order."""
    committed_turns = (
        _successful_turn("first answer"),
        _successful_turn("second answer"),
        _successful_turn("third answer"),
    )
    user_requests = ("first request", "second request", "third request")

    records = to_canonical_history_records(committed_turns, user_requests=user_requests)

    assert records == [
        {"request": "first request", "answer": "first answer"},
        {"request": "second request", "answer": "second answer"},
        {"request": "third request", "answer": "third answer"},
    ]


def test_empty_committed_turn_list_returns_dspy_history_with_empty_messages() -> None:
    """Empty input yields a valid empty ``dspy.History`` (no exception)."""
    history = to_dspy_history([])

    assert type(history) is dspy.History
    assert history.messages == []
    # The empty result must remain compatible with the existing Tool surface.
    assert list(history.messages) == []


def test_cross_session_isolation_excludes_records_from_another_session() -> None:
    """Records from another Session are never present in the result."""
    target_session_id = uuid4()
    other_session_id = uuid4()
    # The two sessions never overlap; the projection operates on the target
    # Session's claimed checkpoint only.
    assert target_session_id != other_session_id

    target_turns = (
        _successful_turn("target answer 1"),
        _successful_turn("target answer 2"),
    )
    target_requests = ("target request 1", "target request 2")
    other_turns = (
        _successful_turn("other answer 1"),
        _successful_turn("other answer 2"),
        _terminal_turn("cancelled", "cancelled", "Turn cancelled"),
    )
    other_requests = ("other request 1", "other request 2", "other request 3")

    target_records = to_canonical_history_records(target_turns, user_requests=target_requests)
    target_history = to_dspy_history(target_turns, user_requests=target_requests)

    # Other-Session answers never leak into the target result.
    for forbidden in (
        "other answer 1",
        "other answer 2",
        "other request 1",
        "other request 2",
        "other request 3",
    ):
        assert forbidden not in {record["request"] for record in target_records}
        assert forbidden not in {record["answer"] for record in target_records}
        assert forbidden not in {value for message in target_history.messages for value in message.values()}

    # A separate call for the other Session still works independently.
    other_records = to_canonical_history_records(other_turns, user_requests=other_requests)
    assert other_records == [
        {"request": "other request 1", "answer": "other answer 1"},
        {"request": "other request 2", "answer": "other answer 2"},
    ]


def test_corrupt_legacy_payloads_raise_value_error() -> None:
    """Corrupt legacy payloads fail closed with ``ValueError`` (no silent truncation)."""
    corrupt_payloads: tuple[Any, ...] = (
        {"request": "r", "answer": "a", "extra": "x"},
        {"answer": "only answer"},
        {"request": "only request"},
        {"request": 1, "answer": "answer"},
        {"request": "request", "answer": 2},
        {"request": "request", "answer": "answer", "trajectory": "leak"},
        [{"request": "r", "answer": "a"}],
        "raw-string-payload",
        42,
        None,
    )
    for payload in corrupt_payloads:
        with pytest.raises(ValueError):
            validate_legacy_records([payload])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_store_level_cross_session_history_isolation() -> None:
    """A store ``begin`` for Session A never loads Session B's committed records.

    Unlike the projection-only isolation check above (which never even feeds
    the other Session's turns into the projection), this drives the real store
    claim path: both Sessions commit Turns into ONE store, then a fresh claim
    for Session A must carry exactly Session A's checkpoint (P52.1(g)).
    """
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome, empty_rlm_usage
    from fleet_rlm.sessions.history import claimed_history_records, dspy_history_for_claim
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        RunClaim,
    )
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    store = InMemoryRunStateStore()
    catalog = InMemorySessionCatalog(store)
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)
    access = TurnAccess(uuid4(), uuid4())
    session_a = await catalog.create(user_id=access.user_id, workspace_id=access.workspace_id, title="A")
    session_b = await catalog.create(user_id=access.user_id, workspace_id=access.workspace_id, title="B")

    async def commit(session_id: object, request: str, answer: str, key: str) -> None:
        claimed = await lifecycle.begin(RunClaim(access, session_id, TurnInput(request), key, uuid4()))
        assert isinstance(claimed, ClaimedRun)
        await lifecycle.finish(
            claimed,
            RLMOutcome(
                "completed",
                PredictionResult(answer, {"answer": answer}, "fleet.default", "1"),
                usage=empty_rlm_usage(),
            ),
        )

    await commit(session_a.id, "alpha request", "alpha answer", "alpha-1")
    await commit(session_b.id, "beta request", "beta answer", "beta-1")

    probe_a = await lifecycle.begin(RunClaim(access, session_a.id, TurnInput("alpha follow-up"), "alpha-2", uuid4()))
    assert isinstance(probe_a, ClaimedRun)
    # The claimed checkpoint is exactly Session A's committed conversation.
    assert [(message.role, message.content) for message in probe_a.history.messages] == [
        ("user", "alpha request"),
        ("assistant", "alpha answer"),
    ]
    turns_a, requests_a = claimed_history_records(probe_a)
    assert requests_a == ("alpha request",)
    assert [turn.text for turn in turns_a] == ["alpha answer"]
    assert list(dspy_history_for_claim(probe_a).messages) == [
        {"request": "alpha request", "answer": "alpha answer"},
    ]

    probe_b = await lifecycle.begin(RunClaim(access, session_b.id, TurnInput("beta follow-up"), "beta-2", uuid4()))
    assert isinstance(probe_b, ClaimedRun)
    assert [(message.role, message.content) for message in probe_b.history.messages] == [
        ("user", "beta request"),
        ("assistant", "beta answer"),
    ]
    assert list(dspy_history_for_claim(probe_b).messages) == [
        {"request": "beta request", "answer": "beta answer"},
    ]


@pytest.mark.asyncio
async def test_sql_store_level_cross_session_history_isolation(tmp_path) -> None:
    """The authoritative SQL store scopes claimed checkpoints to the claimed Session."""
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.history import dspy_history_for_claim
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        RunClaim,
    )

    access = TurnAccess(uuid4(), uuid4())
    session_a, session_b = uuid4(), uuid4()
    engine = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        async with factory() as db, db.begin():
            db.add_all((UserRow(id=access.user_id),))
            await db.flush()
            db.add_all((WorkspaceRow(id=access.workspace_id),))
            await db.flush()
            db.add_all(
                (
                    SessionRow(id=session_a, user_id=access.user_id, workspace_id=access.workspace_id, title="A"),
                    SessionRow(id=session_b, user_id=access.user_id, workspace_id=access.workspace_id, title="B"),
                )
            )
            await db.flush()
        store = SqlAlchemyRunStateStore(factory)

        async def commit(session_id: object, request: str, answer: str, key: str) -> None:
            """
            Commit a successful answer for a session.

            Parameters:
                session_id (object): Identifier of the session receiving the committed turn.
                request (str): User request associated with the turn.
                answer (str): Final answer text for the turn.
                key (str): Key used to claim the turn.
            """
            claimed = await store.begin(RunClaim(access, session_id, TurnInput(request), key, uuid4()))
            assert isinstance(claimed, ClaimedRun)
            await store.commit(
                claimed,
                CommittedTurn(
                    schema_version=1,
                    parts=(UsagePart(value=dict(_EMPTY_USAGE)), TextPart(text=answer)),
                ),
                (),
            )

        await commit(session_a, "alpha request", "alpha answer", "alpha-1")
        await commit(session_b, "beta request", "beta answer", "beta-1")

        probe_a = await store.begin(RunClaim(access, session_a, TurnInput("alpha follow-up"), "alpha-2", uuid4()))
        assert isinstance(probe_a, ClaimedRun)
        # Zero Session B content in Session A's claimed checkpoint.
        assert [(message.role, message.content) for message in probe_a.history.messages] == [
            ("user", "alpha request"),
            ("assistant", "alpha answer"),
        ]
        assert list(dspy_history_for_claim(probe_a).messages) == [
            {"request": "alpha request", "answer": "alpha answer"},
        ]

        probe_b = await store.begin(RunClaim(access, session_b, TurnInput("beta follow-up"), "beta-2", uuid4()))
        assert isinstance(probe_b, ClaimedRun)
        assert [(message.role, message.content) for message in probe_b.history.messages] == [
            ("user", "beta request"),
            ("assistant", "beta answer"),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_timed_out_turn_persists_terminal_status_but_never_enters_committed_history() -> None:
    """A timed-out Turn persists only its durable terminal status, never a History tombstone.

    Drives the real preparation-timeout path (``TurnRuntime.open`` → typed
    ``timeout`` settlement) against the real store, then asserts the next
    claimed checkpoint — and therefore the model-facing canonical projection —
    contains no trace of the timed-out Turn (P52.1(e)). Unlike a cancelled
    Turn (bounded tombstone pair retained for audit), a timed-out Turn
    persists no user/assistant rows at all; only the terminal Run status is
    durable, which is observable by an idempotent retry beginning a FRESH Run.
    """
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome, empty_rlm_usage
    from fleet_rlm.sessions.history import claimed_history_records, dspy_history_for_claim
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        RunClaim,
        RunFailure,
    )
    from fleet_rlm.turns import OpenTurnCommand, TurnRuntime
    from fleet_rlm.turns.preparation import RunPreparationTimeoutError
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id, workspace_id=access.workspace_id, title="timeout"
    )
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)

    first = await lifecycle.begin(RunClaim(access, session.id, TurnInput("before"), "key-before", uuid4()))
    assert isinstance(first, ClaimedRun)
    await lifecycle.finish(
        first,
        RLMOutcome(
            "completed",
            PredictionResult("kept", {"answer": "kept"}, "fleet.default", "1"),
            usage=empty_rlm_usage(),
        ),
    )

    class Preparation:
        async def prepare(self, turn, *, deadline):
            del turn, deadline
            raise RunPreparationTimeoutError("private provider timeout")

    class Runner:
        def stream(self, _execution):
            raise AssertionError("runner must not start")

    timed_out_run_id = uuid4()
    with pytest.raises(RunPreparationTimeoutError):
        await TurnRuntime(lifecycle=lifecycle, preparation=Preparation(), runner=Runner()).open(
            OpenTurnCommand(access, session.id, TurnInput("slow prepare"), "prep-timeout", timed_out_run_id)
        )

    # The timeout is durable and terminal: an idempotent retry does NOT replay
    # and does NOT conflict — it begins a fresh Run under a new run id.
    retry = await lifecycle.begin(RunClaim(access, session.id, TurnInput("slow prepare"), "prep-timeout", uuid4()))
    assert type(retry) is ClaimedRun
    assert retry.run_id != timed_out_run_id
    await lifecycle.finish(retry, RunFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()))

    # No tombstone rows for the timed-out (or failed) Turn: only the committed
    # Turn's pair is listed.
    records = await store.turn_records(session.id, access)
    assert [type(record).__name__ for record in records] == ["UserTurnRecord", "AssistantTurnRecord"]
    assert records[1].committed.text == "kept"

    nxt = await lifecycle.begin(RunClaim(access, session.id, TurnInput("after"), "key-after", uuid4()))
    assert isinstance(nxt, ClaimedRun)
    # The claimed checkpoint carries no trace of the timed-out Turn...
    assert [(message.role, message.content) for message in nxt.history.messages] == [
        ("user", "before"),
        ("assistant", "kept"),
    ]
    # ...and neither does the canonical model-facing projection.
    turns, requests = claimed_history_records(nxt)
    assert requests == ("before",)
    assert [turn.text for turn in turns] == ["kept"]
    assert list(dspy_history_for_claim(nxt).messages) == [{"request": "before", "answer": "kept"}]


@pytest.mark.asyncio
async def test_failed_and_cancelled_turns_never_enter_committed_history_end_to_end() -> None:
    """Failed/cancelled Turns stay bounded audit; the model-facing projection excludes them.

    Drives one committed Turn, one cancelled Turn (settle → complete_settling)
    and one failed Turn through the REAL store/lifecycle, then asserts the
    NEXT claim's raw checkpoint retains only the cancelled tombstone pair for
    audit while ``claimed_history_records``/``dspy_history_for_claim``
    keep exactly the committed conversation (P52.1(c)/(d) end-to-end half).
    """
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome, empty_rlm_usage
    from fleet_rlm.sessions.history import claimed_history_records, dspy_history_for_claim
    from fleet_rlm.sessions.models import TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        RunClaim,
        RunFailure,
    )
    from tests.support.in_memory_stores import InMemoryRunStateStore, InMemorySessionCatalog

    access = TurnAccess(uuid4(), uuid4())
    store = InMemoryRunStateStore()
    session = await InMemorySessionCatalog(store).create(
        user_id=access.user_id, workspace_id=access.workspace_id, title="audit"
    )
    lifecycle = TestingRunSettlement(store, max_artifact_bytes=1024)

    first = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn one"), "key-1", uuid4()))
    assert isinstance(first, ClaimedRun)
    await lifecycle.finish(
        first,
        RLMOutcome(
            "completed",
            PredictionResult("keep me", {"answer": "keep me"}, "fleet.default", "1"),
            usage=empty_rlm_usage(),
        ),
    )

    second = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn two"), "key-2", uuid4()))
    assert isinstance(second, ClaimedRun)
    await lifecycle.settle(second, RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()))
    await lifecycle.complete_settling(second)

    third = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn three"), "key-3", uuid4()))
    assert isinstance(third, ClaimedRun)
    await lifecycle.finish(third, RunFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()))

    fourth = await lifecycle.begin(RunClaim(access, session.id, TurnInput("turn four"), "key-4", uuid4()))
    assert isinstance(fourth, ClaimedRun)
    # The bounded audit projection retains the cancelled tombstone pair...
    assert [(message.role, message.content) for message in fourth.history.messages] == [
        ("user", "turn one"),
        ("assistant", "keep me"),
        ("user", "turn two"),
        ("assistant", "Turn cancelled"),
    ]
    # ...while the canonical model-facing projection keeps only the committed
    # conversation: the cancelled tombstone AND the failed Turn are absent.
    turns, requests = claimed_history_records(fourth)
    assert requests == ("turn one",)
    assert [turn.text for turn in turns] == ["keep me"]
    assert list(dspy_history_for_claim(fourth).messages) == [
        {"request": "turn one", "answer": "keep me"},
    ]

    # The turn listing holds the committed pair plus the cancelled tombstone
    # pair; the failed Turn contributes no rows.
    records = await store.turn_records(session.id, access)
    assert [type(record).__name__ for record in records] == [
        "UserTurnRecord",
        "AssistantTurnRecord",
        "UserTurnRecord",
        "AssistantTurnRecord",
    ]
    assert records[1].committed.text == "keep me"
    assert records[3].committed.text == "Turn cancelled"


def test_claimed_history_records_excludes_tombstone_bearing_checkpoints() -> None:
    """A claimed checkpoint MAY carry bounded failure tombstones; the projection drops them.

    The durable store only persists cancellation tombstones, so failed and
    timed-out tombstone shapes are injected directly into a claimed
    ``SessionHistory`` checkpoint (the exact seam ``claimed_history_records``
    defends) with one successful Turn interleaved.
    """
    from fleet_rlm.sessions.history import claimed_history_records, dspy_history_for_claim
    from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import (
        ClaimedRun,
        _RunClaimToken,
    )

    failed_tombstone = _terminal_turn("failed", "failed", "internal failure details")
    timed_out_tombstone = _terminal_turn("timed_out", "timed_out", "Turn timed out")
    cancelled_tombstone = _terminal_turn("cancelled", "cancelled", "Turn cancelled")
    checkpoint = SessionHistory(
        messages=(
            HistoryMessage("user", "failed request"),
            HistoryMessage("assistant", failed_tombstone.text, failed_tombstone),
            HistoryMessage("user", "kept request"),
            HistoryMessage("assistant", "kept answer"),
            HistoryMessage("user", "timed-out request"),
            HistoryMessage("assistant", timed_out_tombstone.text, timed_out_tombstone),
            HistoryMessage("user", "cancelled request"),
            HistoryMessage("assistant", cancelled_tombstone.text, cancelled_tombstone),
        )
    )

    async def not_cancelled() -> bool:
        return False

    claim = ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("current"),
        checkpoint,
        not_cancelled,
        _RunClaimToken(uuid4(), base_checkpoint_version=2),
    )

    turns, requests = claimed_history_records(claim)

    assert requests == ("kept request",)
    assert [turn.text for turn in turns] == ["kept answer"]
    history = dspy_history_for_claim(claim)
    assert list(history.messages) == [{"request": "kept request", "answer": "kept answer"}]
    # The bounded tombstones never leak into the canonical projection.
    flattened = {value for message in history.messages for value in message.values()}
    assert "internal failure details" not in flattened
    assert "Turn timed out" not in flattened
    assert "Turn cancelled" not in flattened


def _budget_fields(
    *, has_more: bool, truncated: bool, bytes_returned: int, skipped_ordinal: int | None = None
) -> dict[str, object]:
    fields: dict[str, object] = {
        "has_more": has_more,
        "done": not has_more,
        "truncated": truncated,
        "bytes_returned": bytes_returned,
        "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
    }
    if skipped_ordinal is not None:
        fields["skipped_ordinal"] = skipped_ordinal
    return fields


def test_history_tool_pages_canonical_messages_with_stable_ordinals() -> None:
    messages = tuple(
        HistoryMessage(
            "user" if index % 2 == 0 else "assistant",
            f"canonical-{index + 1}\n" + "x" * (index + 1),
        )
        for index in range(5)
    )
    (tool,) = SessionHistoryToolHost(SessionHistory(messages)).as_tools()

    assert tool.name == "read_session_history"
    assert "only when" in tool.desc
    assert "do not read history for self-contained requests" in tool.desc
    assert tool.args == {
        "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    }
    assert "page dictionary" in tool.desc
    assert 'result["messages"]' in tool.desc
    assert "role" in tool.desc
    assert "content" in tool.desc
    first_page = tool(offset=0, limit=2)
    assert first_page == {
        "offset": 0,
        "next_offset": 2,
        "total": 5,
        "messages": [
            {"ordinal": 1, "role": "user", "content": messages[0].content},
            {"ordinal": 2, "role": "assistant", "content": messages[1].content},
        ],
        **_budget_fields(
            has_more=True, truncated=False, bytes_returned=sum(len(m.content.encode("utf-8")) for m in messages[:2])
        ),
    }
    second = tool(offset=2, limit=2)
    assert second["messages"] == [
        {"ordinal": 3, "role": "user", "content": messages[2].content},
        {"ordinal": 4, "role": "assistant", "content": messages[3].content},
    ]
    assert tool(offset=4, limit=20) == {
        "offset": 4,
        "next_offset": None,
        "total": 5,
        "messages": [{"ordinal": 5, "role": "user", "content": messages[4].content}],
        **_budget_fields(has_more=False, truncated=False, bytes_returned=len(messages[4].content.encode("utf-8"))),
    }
    assert tool(offset=9, limit=3) == {
        "offset": 9,
        "next_offset": None,
        "total": 5,
        "messages": [],
        **_budget_fields(has_more=False, truncated=False, bytes_returned=0),
    }


def test_history_tool_stops_mid_page_when_byte_budget_exhausted() -> None:
    chunk = "x" * 150_000
    messages = (
        HistoryMessage("user", chunk),
        HistoryMessage("assistant", chunk),
        HistoryMessage("user", "tail"),
    )
    (tool,) = SessionHistoryToolHost(SessionHistory(messages)).as_tools()

    result = tool(offset=0, limit=20)

    assert result["truncated"] is True
    assert result["messages"] == [
        {"ordinal": 1, "role": "user", "content": chunk},
    ]
    assert result["next_offset"] == 1
    assert result["bytes_returned"] == 150_000

    continuation = tool(offset=result["next_offset"], limit=20)
    assert continuation["messages"] == [
        {"ordinal": 2, "role": "assistant", "content": chunk},
        {"ordinal": 3, "role": "user", "content": "tail"},
    ]
    assert continuation["truncated"] is False
    assert continuation["next_offset"] is None


def test_history_event_view_exposes_page_metadata_without_message_bodies() -> None:
    host = SessionHistoryToolHost(SessionHistory((HistoryMessage("user", "private history body"),)))
    (tool,) = host.as_tools()
    observed: list[object] = []

    result = observe_tool(tool, observed.append, host.event_views()["read_session_history"])(offset=0, limit=1)

    assert result["messages"][0]["content"] == "private history body"
    assert observed[0].input == {"offset": 0, "limit": 1}
    assert observed[1].output == {
        "offset": 0,
        "next_offset": None,
        "total": 1,
        "has_more": False,
        "done": True,
        "truncated": False,
        "bytes_returned": len(b"private history body"),
        "byte_budget": SESSION_HISTORY_RESULT_BYTE_BUDGET,
        "message_count": 1,
    }
    assert "private history body" not in str(observed)


class _SubmittedError(Exception):
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output


class _InProcessInterpreter:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.namespace: dict[str, object] = {}
        self.executed_code: list[str] = []

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.namespace.update(self.tools)
        received = dict(variables or {})
        if "_raw_history" in received and isinstance(received["_raw_history"], bytes):
            try:
                received["_raw_history"] = received["_raw_history"].decode("utf-8")
            except UnicodeDecodeError:
                import base64

                received["_raw_history"] = base64.b64encode(received["_raw_history"]).decode("ascii")
        self.namespace.update(received)
        self.executed_code.append(code)

        def submit(**output: object) -> None:
            raise _SubmittedError(dict(output))

        self.namespace["SUBMIT"] = submit
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec(code, self.namespace, self.namespace)
        except _SubmittedError as final:
            return FinalOutput(final.output)
        return stdout.getvalue()

    def shutdown(self) -> None:
        return None


def test_transport_diagnostics_redact_complete_conversation_bodies() -> None:
    history = CommittedSessionHistory([{"request": "SECRET_REQUEST_BODY", "answer": "SECRET_ANSWER_BODY"}])

    rendered = repr(history)
    assert rendered == "CommittedSessionHistory(messages=1)"
    assert str(history) == rendered
    assert "SECRET_REQUEST_BODY" not in rendered
    assert "SECRET_ANSWER_BODY" not in rendered


def test_transport_rejects_non_canonical_records_without_truncation() -> None:
    for bad in (
        [{"request": "r", "answer": "a", "extra": "x"}],
        [{"request": 1, "answer": "a"}],
        [{"prompt": "r", "answer": "a"}],
        [{"request": "r"}],
        ["not-a-record"],
    ):
        with pytest.raises(ValueError, match="committed Session History"):
            CommittedSessionHistory(bad)


def test_transport_round_trips_complete_unicode_multiline_records_in_sandbox() -> None:
    records = [
        {"request": "first Ω漢字🧪\nsecond line", "answer": "café — résumé ✓"},
        *({"request": f"bulk {ordinal}", "answer": f"reply {ordinal}\npara {ordinal}"} for ordinal in range(200)),
    ]
    history = CommittedSessionHistory(records)
    assert committed_session_history_payload(history) == records
    assert "café" not in history.rlm_preview()
    assert len(history.rlm_preview()) <= 500

    interpreter = _InProcessInterpreter()
    setup = history.sandbox_setup()
    assignment = history.sandbox_assignment("history", "_raw_history")
    assert "_raw_history" in assignment and "history.messages" not in assignment
    interpreter.execute("\n".join((setup, assignment)), variables={"_raw_history": history.to_sandbox()})

    restored = interpreter.namespace["history"]
    assert type(restored).__name__ == "_FleetCommittedHistory"
    assert list(restored.messages) == records
    assert restored.messages[0]["answer"] == "café — résumé ✓"
    assert "_fleet_load_committed_history" not in interpreter.namespace


@pytest.mark.asyncio
async def test_native_rlm_acall_sees_complete_history_through_the_transport() -> None:
    records = [
        {"request": "opening request", "answer": "opening answer"},
        {"request": "latest request", "answer": "latest answer"},
    ]
    history = CommittedSessionHistory(records)

    class SessionRLMContract(dspy.Signature):
        request: str = dspy.InputField()
        history: dspy.History = dspy.InputField()
        answer: str = dspy.OutputField()

    class _Actions:
        def __init__(self) -> None:
            self.calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            return dspy.Prediction(
                reasoning="Read the complete committed conversation from the transported value.",
                code=(
                    "assert type(history).__name__ == '_FleetCommittedHistory'\n"
                    f"assert history.messages == {records!r}\n"
                    "assert history.messages[-1]['answer'] == 'latest answer'\n"
                    "SUBMIT(answer=history.messages[-1]['answer'])"
                ),
            )

    actions = _Actions()
    rlm = dspy.RLM(SessionRLMContract, max_iters=1)
    rlm.generate_action = actions
    interpreter = _InProcessInterpreter()

    prediction = await rlm.acall(interpreter_factory=lambda: interpreter, request="current", history=history)

    assert actions.calls == 1
    assert prediction.answer == "latest answer"
    assert any("_raw_history" in code for code in interpreter.executed_code)


def test_history_materializes_before_nested_provider_404_failure() -> None:
    history = CommittedSessionHistory([{"request": "prior", "answer": "settled"}])
    interpreter = _InProcessInterpreter()
    interpreter.execute(
        "\n".join((history.sandbox_setup(), history.sandbox_assignment("history", "_raw_history"))),
        variables={"_raw_history": history.to_sandbox()},
    )
    assert interpreter.namespace["history"].messages == [{"request": "prior", "answer": "settled"}]

    class _ProviderNotFoundError(Exception):
        status_code = 404

    try:
        raise RuntimeError("LMUnsupportedModelError: Error code: 404") from _ProviderNotFoundError()
    except RuntimeError as raised:
        assert normalize_turn_failure(raised).cause_type == "provider_not_found"
