"""Thin use-case: assemble turn context and stream RuntimeEvents from RLMRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.context_builder import (
    OfflineContextBuilder,
    ephemeral_lease,
    rebind_turn_context,
)
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.outcome import TurnExecutionOutcome
from fleet_rlm_clean.rlm.runner import RLMRunner, TurnEventStream
from fleet_rlm_clean.sessions.checkpoints import TurnClaim
from fleet_rlm_clean.sessions.errors import SessionAccessDenied, SessionNotFoundError
from fleet_rlm_clean.sessions.history import turns_to_history
from fleet_rlm_clean.sessions.locks import SessionLockRegistry

__all__ = [
    "SessionStore",
    "TurnCoordinator",
    "TurnRunner",
    "ephemeral_lease",
    "rebind_turn_context",
]


class TurnRunner(Protocol):
    def stream(self, context: RLMTurnContext) -> TurnEventStream: ...


class SessionStore(Protocol):
    async def load(self, session_id: Any) -> Any: ...

    async def claim_turn(
        self,
        session_id: Any,
        *,
        idempotency_key: str | None = None,
        run_id: Any | None = None,
    ) -> TurnClaim: ...

    async def begin_run(
        self,
        session_id: Any,
        *,
        idempotency_key: str | None = None,
        run_id: Any | None = None,
    ) -> Any: ...

    async def append_completed_exchange(
        self,
        session_id: Any,
        *,
        user_text: str,
        assistant_text: str,
        run_id: Any | None = None,
        expected_checkpoint_version: int | None = None,
    ) -> Any: ...

    async def finish_failed_run(
        self,
        session_id: Any,
        run_id: Any,
        *,
        message: str | None = None,
    ) -> Any: ...


def public_terminal_from_outcome(
    recorder: EventRecorder,
    outcome: TurnExecutionOutcome,
    *,
    checkpoint_version: int | None = None,
    idempotent_replay: bool = False,
) -> RuntimeEvent:
    """Map an internal outcome to exactly one public terminal RuntimeEvent."""
    if outcome.succeeded:
        payload: dict[str, Any] = {
            "status": "completed",
            "assistant_text": outcome.assistant_text,
        }
        if outcome.duration_ms is not None:
            payload["duration_ms"] = outcome.duration_ms
        if checkpoint_version is not None:
            payload["checkpoint_version"] = checkpoint_version
        if idempotent_replay:
            payload["idempotent_replay"] = True
        return recorder.emit(RuntimeEventKind.RUN_COMPLETED, payload)

    payload = {
        "status": outcome.terminal_status,
        "message": outcome.public_error_message or "turn failed",
    }
    if outcome.duration_ms is not None:
        payload["duration_ms"] = outcome.duration_ms
    return recorder.emit(RuntimeEventKind.ERROR, payload)


class TurnCoordinator:
    """Application service for one chat turn. No DSPy or Daytona SDK calls here."""

    def __init__(
        self,
        *,
        runner: TurnRunner | None = None,
        context_builder: Any | None = None,
        session_repository: SessionStore | None = None,
        locks: SessionLockRegistry | None = None,
    ) -> None:
        self._runner: TurnRunner = runner if runner is not None else RLMRunner()
        self._context_builder: Any = OfflineContextBuilder() if context_builder is None else context_builder
        self._sessions = session_repository
        self._locks = locks if locks is not None else SessionLockRegistry()

    def _build_context(self, command: ChatTurnCommand) -> RLMTurnContext:
        builder = self._context_builder
        build = getattr(builder, "build", None)
        if callable(build):
            return build(command)
        if callable(builder):
            return builder(command)
        msg = "context_builder must be callable or provide build(command)"
        raise TypeError(msg)

    async def stream(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        """Stream public RuntimeEvents for a single chat command."""
        if not command.message or not command.message.strip():
            msg = "message is required"
            raise ValueError(msg)

        if self._sessions is None:
            context = self._build_context(command)
            async for event in self._stream_runner_then_terminal(context, persist=None):
                yield event
            return

        async with self._locks.hold(command.session_id):
            async for event in self._stream_locked(command):
                yield event

    async def _stream_locked(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        assert self._sessions is not None
        snapshot = await self._sessions.load(command.session_id)
        sess = snapshot.session
        if sess.user_id != command.user_id or sess.workspace_id != command.workspace_id:
            raise SessionNotFoundError(f"session {command.session_id} not found") from SessionAccessDenied()

        history = turns_to_history(snapshot.turns)
        preferred_run_id = uuid4()
        claim = await self._claim(command, preferred_run_id)
        if claim.replay:
            async for event in self._replay_events(
                session_id=command.session_id,
                run_id=claim.run_id,
                assistant_text=claim.assistant_text or "",
            ):
                yield event
            return

        built = self._build_context(command)
        context = rebind_turn_context(
            built,
            run_id=claim.run_id,
            history=history,
        )
        base_version = claim.base_checkpoint_version
        async for event in self._stream_runner_then_terminal(
            context,
            persist=_PersistArgs(
                command=command,
                context=context,
                expected_checkpoint_version=base_version,
            ),
        ):
            yield event

    async def _stream_runner_then_terminal(
        self,
        context: RLMTurnContext,
        *,
        persist: _PersistArgs | None,
    ) -> AsyncIterator[RuntimeEvent]:
        stream = self._runner.stream(context)
        last_sequence = 0
        async for event in stream:
            if event.kind in {RuntimeEventKind.RUN_COMPLETED, RuntimeEventKind.ERROR}:
                # Protocol violation if a runner yields public terminals.
                msg = "runner emitted a public terminal event"
                raise RuntimeError(msg)
            last_sequence = event.sequence
            yield event

        outcome = stream.outcome
        if outcome is None:
            outcome = TurnExecutionOutcome(
                terminal_status="failed",
                public_error_message="stream ended without outcome",
            )

        checkpoint_version: int | None = None
        if persist is not None:
            checkpoint_version = await self._persist_outcome(persist, outcome)

        recorder = EventRecorder(
            run_id=context.run_id,
            session_id=context.session_id,
            start_sequence=last_sequence,
        )
        yield public_terminal_from_outcome(
            recorder,
            outcome,
            checkpoint_version=checkpoint_version,
        )

    async def _claim(self, command: ChatTurnCommand, preferred_run_id: UUID) -> TurnClaim:
        assert self._sessions is not None
        claim_turn = getattr(self._sessions, "claim_turn", None)
        if callable(claim_turn):
            return await claim_turn(
                command.session_id,
                idempotency_key=command.idempotency_key or None,
                run_id=preferred_run_id,
            )
        run_id = await self._sessions.begin_run(
            command.session_id,
            idempotency_key=command.idempotency_key or None,
            run_id=preferred_run_id,
        )
        snap = await self._sessions.load(command.session_id)
        return TurnClaim(
            run_id=run_id,
            base_checkpoint_version=int(snap.session.checkpoint_version),
            replay=False,
        )

    async def _replay_events(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        assistant_text: str,
    ) -> AsyncIterator[RuntimeEvent]:
        recorder = EventRecorder(run_id=run_id, session_id=session_id)
        yield recorder.emit(RuntimeEventKind.STATUS, {"message": "idempotent_replay"})
        if assistant_text:
            yield recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": assistant_text})
            yield recorder.emit(RuntimeEventKind.TEXT_COMPLETED, {"text": assistant_text})
        yield public_terminal_from_outcome(
            recorder,
            TurnExecutionOutcome(
                terminal_status="completed",
                assistant_text=assistant_text,
            ),
            idempotent_replay=True,
        )

    async def _persist_outcome(
        self,
        args: _PersistArgs,
        outcome: TurnExecutionOutcome,
    ) -> int | None:
        assert self._sessions is not None
        command = args.command
        context = args.context
        if outcome.succeeded:
            snapshot = await self._sessions.append_completed_exchange(
                command.session_id,
                user_text=command.message,
                assistant_text=outcome.assistant_text,
                run_id=context.run_id,
                expected_checkpoint_version=args.expected_checkpoint_version,
            )
            session = getattr(snapshot, "session", None)
            if session is not None:
                return int(getattr(session, "checkpoint_version", 0) or 0)
            return None

        await self._sessions.finish_failed_run(
            command.session_id,
            context.run_id,
            message=outcome.public_error_message or "turn failed",
        )
        return None


class _PersistArgs:
    __slots__ = ("command", "context", "expected_checkpoint_version")

    def __init__(
        self,
        *,
        command: ChatTurnCommand,
        context: RLMTurnContext,
        expected_checkpoint_version: int | None,
    ) -> None:
        self.command = command
        self.context = context
        self.expected_checkpoint_version = expected_checkpoint_version
