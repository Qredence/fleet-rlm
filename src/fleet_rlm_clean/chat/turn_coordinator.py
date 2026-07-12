"""Thin use-case: assemble turn context and stream RuntimeEvents from RLMRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import InterpreterLeaseLike, RLMTurnContext
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import RLMRunner
from fleet_rlm_clean.sessions.checkpoints import TurnClaim
from fleet_rlm_clean.sessions.history import turns_to_history
from fleet_rlm_clean.sessions.locks import SessionLockRegistry


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


class _EphemeralLease:
    """Kernel-phase lease: holds an interpreter handle; release is idempotent."""

    def __init__(self, interpreter: Any) -> None:
        self.interpreter = interpreter
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        shutdown = getattr(self.interpreter, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _default_kernel_context(command: ChatTurnCommand) -> RLMTurnContext:
    """Build a kernel context with placeholder models/interpreter (no live providers)."""
    return RLMTurnContext(
        run_id=uuid4(),
        session_id=command.session_id,
        user_id=command.user_id,
        workspace_id=command.workspace_id,
        request=command.message,
        models=RLMModelBundle(root_lm=MagicMock(name="root_lm"), sub_lm=MagicMock(name="sub_lm")),
        budget=RLMBudget(),
        lease=_EphemeralLease(MagicMock(name="interpreter")),
    )


class TurnCoordinator:
    """Application service for one chat turn. No DSPy or Daytona SDK calls here."""

    def __init__(
        self,
        *,
        runner: TurnRunner | None = None,
        context_builder: Callable[[ChatTurnCommand], RLMTurnContext] | None = None,
        session_repository: SessionStore | None = None,
        locks: SessionLockRegistry | None = None,
    ) -> None:
        self._runner: TurnRunner = runner if runner is not None else RLMRunner()
        self._context_builder = (
            context_builder if context_builder is not None else _default_kernel_context
        )
        self._sessions = session_repository
        self._locks = locks if locks is not None else SessionLockRegistry()

    async def stream(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        """Stream public RuntimeEvents for a single chat command."""
        if not command.message or not command.message.strip():
            msg = "message is required"
            raise ValueError(msg)

        if self._sessions is None:
            context = self._context_builder(command)
            async for event in self._runner.stream(context):
                yield event
            return

        async with self._locks.hold(command.session_id):
            async for event in self._stream_locked(command):
                yield event

    async def _stream_locked(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        assert self._sessions is not None
        context = self._context_builder(command)
        snapshot = await self._sessions.load(command.session_id)
        # Isolation: reject cross-workspace/user before runtime resources run
        from fleet_rlm_clean.api.auth_errors import SessionAccessDenied
        from fleet_rlm_clean.sessions.errors import SessionNotFoundError

        sess = snapshot.session
        if sess.user_id != command.user_id or sess.workspace_id != command.workspace_id:
            # Same public shape as missing session
            raise SessionNotFoundError(f"session {command.session_id} not found") from SessionAccessDenied()
        history = turns_to_history(snapshot.turns)

        claim = await self._claim(command, context.run_id)
        if claim.replay:
            async for event in self._replay_events(
                session_id=command.session_id,
                run_id=claim.run_id,
                assistant_text=claim.assistant_text or "",
            ):
                yield event
            return

        context = RLMTurnContext(
            run_id=claim.run_id,
            session_id=context.session_id,
            user_id=context.user_id,
            workspace_id=context.workspace_id,
            request=context.request,
            models=context.models,
            budget=context.budget,
            lease=context.lease,
            history=history,
            session_summary=context.session_summary,
            skill_cards=context.skill_cards,
            attachments=context.attachments,
            artifacts=context.artifacts,
            tools=context.tools,
            skill_tool_host=getattr(context, "skill_tool_host", None),
            file_tool_host=getattr(context, "file_tool_host", None),
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


def ephemeral_lease(interpreter: Any) -> InterpreterLeaseLike:
    """Public helper for tests and kernel wiring."""
    return _EphemeralLease(interpreter)
