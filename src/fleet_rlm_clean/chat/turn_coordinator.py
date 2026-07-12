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
from fleet_rlm_clean.rlm.runner import RLMRunner
from fleet_rlm_clean.sessions.checkpoints import TurnClaim
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
    def stream(self, context: RLMTurnContext) -> AsyncIterator[RuntimeEvent]: ...


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
        # Callable[[ChatTurnCommand], RLMTurnContext] or object with .build(command)
        self._context_builder: Any = (
            OfflineContextBuilder() if context_builder is None else context_builder
        )
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
            async for event in self._runner.stream(context):
                yield event
            return

        async with self._locks.hold(command.session_id):
            async for event in self._stream_locked(command):
                yield event

    async def _stream_locked(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        assert self._sessions is not None
        # Isolation first: load + ownership before any context builder / Sandbox acquire
        from fleet_rlm_clean.sessions.errors import SessionAccessDenied, SessionNotFoundError

        snapshot = await self._sessions.load(command.session_id)
        sess = snapshot.session
        if sess.user_id != command.user_id or sess.workspace_id != command.workspace_id:
            # Same public shape as missing session (no existence leak)
            raise SessionNotFoundError(
                f"session {command.session_id} not found"
            ) from SessionAccessDenied()

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

        # Only after authz: build context (may acquire Daytona lease), then rebind claim
        built = self._build_context(command)
        context = rebind_turn_context(
            built,
            run_id=claim.run_id,
            history=history,
        )
        base_version = claim.base_checkpoint_version
        terminal: RuntimeEvent | None = None
        try:
            async for event in self._runner.stream(context):
                if event.kind in {RuntimeEventKind.RUN_COMPLETED, RuntimeEventKind.ERROR}:
                    terminal = event
                yield event
        finally:
            await self._persist_terminal(
                command,
                context,
                terminal,
                expected_checkpoint_version=base_version,
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
        # Fallback for older store doubles
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
        yield recorder.emit(
            RuntimeEventKind.RUN_COMPLETED,
            {
                "status": "completed",
                "assistant_text": assistant_text,
                "idempotent_replay": True,
            },
        )

    async def _persist_terminal(
        self,
        command: ChatTurnCommand,
        context: RLMTurnContext,
        terminal: RuntimeEvent | None,
        *,
        expected_checkpoint_version: int | None,
    ) -> None:
        assert self._sessions is not None
        if terminal is None:
            await self._sessions.finish_failed_run(
                command.session_id,
                context.run_id,
                message="stream ended without terminal event",
            )
            return
        if terminal.kind == RuntimeEventKind.RUN_COMPLETED:
            assistant = str(terminal.payload.get("assistant_text") or "")
            await self._sessions.append_completed_exchange(
                command.session_id,
                user_text=command.message,
                assistant_text=assistant,
                run_id=context.run_id,
                expected_checkpoint_version=expected_checkpoint_version,
            )
            return
        message = str(terminal.payload.get("message") or "turn failed")
        await self._sessions.finish_failed_run(
            command.session_id,
            context.run_id,
            message=message,
        )
