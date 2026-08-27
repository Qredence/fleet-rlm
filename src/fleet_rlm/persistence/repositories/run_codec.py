"""Internal ORM/JSON codec for the deep Run state-repository facade.

This module centralizes persisted status/claim decoding and conversion between
Turn/Result domain values and SQL rows. Transaction and lock ownership remain
with `SqlAlchemyRunStateStore` and `InMemoryRunStateStore`; these helpers never
open sessions or commits.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.chat.run_claim import (
    ClaimCommand,
    ClaimFailure,
    ClaimFailureCode,
    ClaimState,
    ClaimStatus,
    ClaimTransition,
    failure_code_for_terminal_status,
)
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    FailedRunReceipt,
    RunFailure,
    RunFailureCode,
    RunStateError,
)
from fleet_rlm.chat.turn_detail_policy import commit_cancelled_tombstone
from fleet_rlm.persistence.models import ArtifactRow, RunRow, TurnRow
from fleet_rlm.rlm.dspy_contract import RLMUsage, empty_rlm_usage
from fleet_rlm.sessions.committed_turn import CommittedTurn, CommittedTurnCodec
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory, TurnInput, TurnInputCodec

_MEMORY_RUN_STATE = Any


def _decode_failure_status(value: str) -> Literal["failed", "cancelled", "timeout"]:
    """Validate and return a persisted Run failure status."""
    if value in {"failed", "cancelled", "timeout"}:
        return value
    raise RunStateError("persisted Run has an invalid failure status")


def _decode_failure_code(
    value: str | None,
    *,
    status: Literal["failed", "cancelled", "timeout"],
) -> RunFailureCode:
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    if value in {None, "failed"}:
        return failure_code_for_terminal_status(status)
    raise RunStateError("persisted Run has an invalid failure code")


def _decode_claim_status(value: str) -> ClaimStatus:
    if value in {"running", "settling", "completed", "failed", "cancelled", "timeout"}:
        return value
    raise RunStateError("persisted Run has an invalid claim status")


def _decode_claim_code(value: str | None) -> ClaimFailureCode | None:
    if value is None:
        return None
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    raise RunStateError("persisted Run has an invalid failure code")


def _claim_failure(failure: RunFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


def _command_usage(command: ClaimCommand) -> RLMUsage | None:
    if hasattr(command, "usage"):
        return cast(RLMUsage, command.usage)
    return None


def _turn_failure(intent: ClaimFailure, usage: RLMUsage) -> RunFailure:
    return RunFailure(intent.status, intent.code, intent.public_message, usage)


def _memory_claim_state(run: _MEMORY_RUN_STATE) -> ClaimState:
    intent = _claim_failure(run.terminal_intent) if run.terminal_intent is not None else None
    return ClaimState(_decode_claim_status(run.status), _decode_claim_code(run.failure_code), intent)


def _row_claim_state(run: RunRow) -> ClaimState:
    intent = None
    if run.terminal_intent is not None:
        status = _decode_failure_status(run.terminal_intent)
        intent = ClaimFailure(
            status,
            _decode_failure_code(run.failure_code, status=status),
            run.failure_public_message or "Turn failed",
        )
    return ClaimState(_decode_claim_status(run.status), _decode_claim_code(run.failure_code), intent)


def _transition_receipt(run_id: UUID, decision: ClaimTransition) -> FailedRunReceipt:
    status = _decode_failure_status(decision.status)
    return FailedRunReceipt(
        run_id,
        status,
        _decode_failure_code(decision.failure_code, status=status),
        decision.public_message,
        decision.finalized,
    )


def _apply_memory_next_state(
    run: _MEMORY_RUN_STATE,
    next_state: ClaimState,
    *,
    usage: RLMUsage | None = None,
) -> None:
    run.status = cast(Any, next_state.status)
    run.failure_code = cast(RunFailureCode, next_state.failure_code)
    if next_state.intent is not None:
        if usage is None:
            raise RunStateError("claim intent application requires usage")
        run.terminal_intent = _turn_failure(next_state.intent, usage)


def _apply_row_next_state(
    run: RunRow,
    next_state: ClaimState,
    *,
    public_message: str,
    usage: RLMUsage | None = None,
) -> None:
    run.status = next_state.status
    run.failure_code = next_state.failure_code
    run.failure_public_message = public_message
    if usage is not None:
        run.failure_usage_json = dict(usage)
    if next_state.intent is not None:
        run.terminal_intent = next_state.intent.status


def _encode_turn_input(value: TurnInput) -> dict[str, Any]:
    return TurnInputCodec.encode(value)


def _decode_turn_input(value: object) -> TurnInput:
    return TurnInputCodec.decode(value)


def _encode_committed_turn(value: CommittedTurn) -> dict[str, Any]:
    return CommittedTurnCodec.encode(value)


def _decode_committed_turn(value: object) -> CommittedTurn:
    return CommittedTurnCodec.decode(value)


def _cancelled_tombstone(usage: RLMUsage) -> CommittedTurn:
    return commit_cancelled_tombstone(usage)


def _committed_turn_rows(
    *,
    run_id: UUID,
    session_id: UUID,
    run_input: TurnInput,
    committed: CommittedTurn,
    first_sequence: int,
) -> tuple[TurnRow, TurnRow]:
    return (
        TurnRow(
            id=uuid4(),
            session_id=session_id,
            run_id=run_id,
            sequence=first_sequence,
            role="user",
            user_input_json=_encode_turn_input(run_input),
            committed_turn_json=None,
        ),
        TurnRow(
            id=run_id,
            session_id=session_id,
            run_id=run_id,
            sequence=first_sequence + 1,
            role="assistant",
            user_input_json=None,
            committed_turn_json=_encode_committed_turn(committed),
        ),
    )


def _artifact_row_for_commit(run: ClaimedRun, artifact: PromotedArtifact) -> ArtifactRow:
    ref = artifact.ref
    return ArtifactRow(
        id=ref.id,
        user_id=run.access.user_id,
        workspace_id=run.access.workspace_id,
        session_id=ref.session_id,
        run_id=ref.run_id,
        kind=ref.kind,
        title=ref.title,
        media_type=ref.media_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        storage_ref=artifact.storage_ref,
    )


def _cancel_tombstone_rows(
    *,
    run: RunRow,
    turn_input: TurnInput | None,
    next_sequence: int,
) -> tuple[TurnRow, ...]:
    usage: RLMUsage = cast(RLMUsage, run.failure_usage_json) if run.failure_usage_json else empty_rlm_usage()
    rows: list[TurnRow] = []
    if turn_input is not None:
        rows.append(
            TurnRow(
                id=uuid4(),
                session_id=run.session_id,
                run_id=run.id,
                sequence=next_sequence,
                role="user",
                user_input_json=_encode_turn_input(turn_input),
                committed_turn_json=None,
            )
        )
        next_sequence += 1
    rows.append(
        TurnRow(
            id=run.id,
            session_id=run.session_id,
            run_id=run.id,
            sequence=next_sequence,
            role="assistant",
            user_input_json=None,
            committed_turn_json=_encode_committed_turn(_cancelled_tombstone(usage)),
        )
    )
    return tuple(rows)


def _artifact_ref_from_row(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef(
        row.id,
        row.session_id,
        row.run_id,
        parse_kind(row.kind),
        row.title,
        row.media_type,
        row.byte_size,
        row.checksum_sha256 or "",
    )


def _artifact_refs_from_rows(rows: Iterable[ArtifactRow]) -> tuple[ArtifactRef, ...]:
    return tuple(_artifact_ref_from_row(row) for row in rows)


def _history_from_turn_rows(rows: Sequence[TurnRow]) -> SessionHistory:
    """Project durable Turn rows into the Session History message snapshot."""
    messages: list[HistoryMessage] = []
    for row in rows:
        if row.role == "user" and row.user_input_json is not None:
            messages.append(HistoryMessage("user", _decode_turn_input(row.user_input_json).text))
        elif row.role == "assistant" and row.committed_turn_json is not None:
            committed = _decode_committed_turn(row.committed_turn_json)
            messages.append(HistoryMessage("assistant", committed.text, committed))
        else:
            raise RunStateError("stored Turn shape is invalid")
    return SessionHistory(tuple(messages))
