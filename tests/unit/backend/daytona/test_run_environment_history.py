"""P43.7 + P44.5 production wiring: Daytona composition uses ``CommittedSessionHistory``.

The Dayona broker cannot inject a raw ``dspy.History`` Pydantic value into
a Sandbox. The Dayona composition therefore projects the claimed Session
checkpoint to the same canonical ``{"request", "answer"}`` records used
by the in-process composition and wraps them in the P43.7
``CommittedSessionHistory`` transport so the interpreter can reconstruct
the conversation inside the Sandbox.

The test pins two contracts:

* ``daytona/run_environment.py`` exposes a helper that returns a
  ``CommittedSessionHistory`` (not ``dspy.History``) for one
  ``ClaimedRun``.
* The records passed to the transport equal the canonical records
  returned by ``to_canonical_history_records`` for the same checkpoint.
"""

from __future__ import annotations

from uuid import uuid4

import dspy

from fleet_rlm.sessions.history import to_canonical_history_records
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnAccess, TurnInput


def _make_claim(*, history_messages: tuple[HistoryMessage, ...] = ()):
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("current"),
        SessionHistory(messages=history_messages),
        not_cancelled,
        _RunClaimToken(uuid4(), base_checkpoint_version=2),
    )


def test_daytona_run_environment_exposes_committed_session_history_helper() -> None:
    """``daytona/run_environment.py`` exposes a ``CommittedSessionHistory`` builder."""

    from fleet_rlm.daytona import run_environment

    assert hasattr(run_environment, "build_committed_session_history_for_claim")
    helper = run_environment.build_committed_session_history_for_claim
    assert callable(helper)


def test_daytona_helper_returns_committed_session_history_not_dspy_history() -> None:
    """The helper returns ``CommittedSessionHistory`` and never ``dspy.History``."""

    from fleet_rlm.daytona.run_environment import build_committed_session_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
        )
    )
    history = build_committed_session_history_for_claim(claim)

    assert type(history) is CommittedSessionHistory
    # The transport is NOT the in-process ``dspy.History`` type; the
    # Dayona broker requires the ``SandboxSerializable`` wrapper.
    assert not isinstance(history, dspy.History)


def test_daytona_helper_records_equal_canonical_history_records() -> None:
    """The Dayona transport records equal :func:`to_canonical_history_records` output."""

    from fleet_rlm.chat.run_preparation import _claim_history_records
    from fleet_rlm.daytona.run_environment import build_committed_session_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
            HistoryMessage("user", "next user request"),
            HistoryMessage("assistant", "next assistant answer"),
        )
    )
    transport = build_committed_session_history_for_claim(claim)

    committed_turns, user_requests = _claim_history_records(claim)
    canonical = to_canonical_history_records(committed_turns, user_requests=user_requests)

    # Records are deep-equal and the transport carries them in order.
    assert list(transport.messages) == canonical
    assert [dict(record) for record in transport.messages] == [
        {"request": "earlier user request", "answer": "earlier assistant answer"},
        {"request": "next user request", "answer": "next assistant answer"},
    ]


def test_daytona_helper_skips_orphan_user_messages_without_assistant_answers() -> None:
    """Orphan user messages never pair with the next assistant answer."""

    from fleet_rlm.daytona.run_environment import build_committed_session_history_for_claim

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "first user request"),
            # No assistant message between the two users; the second user
            # message is the start of the uncommitted Turn, so the first
            # user request is dropped.
            HistoryMessage("user", "second user request"),
            HistoryMessage("assistant", "second assistant answer"),
        )
    )
    transport = build_committed_session_history_for_claim(claim)

    assert [dict(record) for record in transport.messages] == [
        {"request": "second user request", "answer": "second assistant answer"}
    ]


def test_daytona_helper_returns_empty_history_for_fresh_session() -> None:
    """A claim with no committed Turns still produces a valid empty transport."""

    from fleet_rlm.daytona.run_environment import build_committed_session_history_for_claim

    claim = _make_claim(history_messages=())
    transport = build_committed_session_history_for_claim(claim)

    assert type(transport) is CommittedSessionHistory
    assert list(transport.messages) == []
