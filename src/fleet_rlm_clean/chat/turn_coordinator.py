"""Thin use-case: assemble turn context and stream RuntimeEvents from RLMRunner."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm_clean.artifacts.models import ArtifactCandidate
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

    async def commit_completed_turn(
        self,
        session_id: Any,
        *,
        user_text: str,
        assistant_text: str,
        run_id: Any | None = None,
        expected_checkpoint_version: int | None = None,
        artifact_candidates: tuple[ArtifactCandidate, ...] = (),
    ) -> Any: ...

    async def finish_failed_run(
        self,
        session_id: Any,
        run_id: Any,
        *,
        message: str | None = None,
    ) -> Any: ...

    async def is_cancel_requested(self, run_id: Any) -> bool: ...


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

    async def _abuild_context(
        self,
        command: ChatTurnCommand,
        *,
        run_id: UUID | None = None,
    ) -> RLMTurnContext:
        """Build context; await async builders (e.g. LiveKernelResources.build_context)."""

        builder = self._context_builder
        for name in ("build_context", "abuild", "build"):
            method = getattr(builder, name, None)
            if not callable(method):
                continue
            try:
                accepts_run_id = "run_id" in inspect.signature(method).parameters
            except (TypeError, ValueError):
                accepts_run_id = False
            result = method(command, run_id=run_id) if run_id is not None and accepts_run_id else method(command)
            if inspect.isawaitable(result):
                return await result
            return result
        if callable(builder):
            result = builder(command)
            if inspect.isawaitable(result):
                return await result
            return result
        msg = "context_builder must be callable or provide build/build_context"
        raise TypeError(msg)

    async def stream(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        """Stream public RuntimeEvents for a single chat command."""
        if not command.message or not command.message.strip():
            msg = "message is required"
            raise ValueError(msg)

        if self._sessions is None:
            context = await self._abuild_context(command)
            async for event in self._stream_runner_then_terminal(context, persist=None):
                yield event
            return

        async with self._locks.hold(command.session_id):
            async for event in self._stream_locked(command):
                yield event

    async def _stream_locked(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        assert self._sessions is not None
        preferred_run_id = uuid4()
        try:
            snapshot = await self._sessions.load(command.session_id)
            sess = snapshot.session
            if sess.user_id != command.user_id or sess.workspace_id != command.workspace_id:
                raise SessionNotFoundError(f"session {command.session_id} not found") from SessionAccessDenied()
            history = turns_to_history(snapshot.turns)
            claim = await self._claim(command, preferred_run_id)
        except Exception:  # noqa: BLE001 - preparation details are never public
            try:
                await self._sessions.finish_failed_run(
                    command.session_id,
                    preferred_run_id,
                    message="Turn could not be prepared",
                )
            except Exception:  # noqa: BLE001 - preserve exactly one public terminal
                pass
            recorder = EventRecorder(run_id=preferred_run_id, session_id=command.session_id)
            yield recorder.emit(
                RuntimeEventKind.ERROR,
                {"status": "failed", "message": "Turn could not be prepared"},
            )
            return
        if claim.replay:
            async for event in self._replay_events(
                session_id=command.session_id,
                run_id=claim.run_id,
                assistant_text=claim.assistant_text or "",
            ):
                yield event
            return

        sessions = self._sessions

        try:
            built = await self._abuild_context(command, run_id=claim.run_id)
        except Exception:  # noqa: BLE001 - preparation details are never public
            try:
                await sessions.finish_failed_run(
                    command.session_id,
                    claim.run_id,
                    message="Turn could not be prepared",
                )
            except Exception:  # noqa: BLE001 - preserve exactly one public terminal
                pass
            recorder = EventRecorder(run_id=claim.run_id, session_id=command.session_id)
            yield recorder.emit(
                RuntimeEventKind.ERROR,
                {"status": "failed", "message": "Turn could not be prepared"},
            )
            return

        async def _cancel_probe(run_id: UUID) -> bool:
            return await sessions.is_cancel_requested(run_id)

        context = rebind_turn_context(
            built,
            run_id=claim.run_id,
            history=history,
            cancel_probe=_cancel_probe,
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
        last_sequence = 0
        try:
            stream = self._runner.stream(context)
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

            recorder = EventRecorder(
                run_id=context.run_id,
                session_id=context.session_id,
                start_sequence=last_sequence,
            )
            checkpoint_version: int | None = None
            if persist is not None:
                try:
                    checkpoint_version = await self._persist_outcome(persist, outcome)
                except Exception:  # noqa: BLE001 - persistence details are never public
                    yield public_terminal_from_outcome(
                        recorder,
                        TurnExecutionOutcome(
                            terminal_status="failed",
                            public_error_message="Turn could not be committed",
                            duration_ms=outcome.duration_ms,
                        ),
                    )
                    return

            if outcome.succeeded and persist is not None:
                for candidate in outcome.artifact_candidates:
                    yield recorder.emit(
                        RuntimeEventKind.ARTIFACT_CREATED,
                        {
                            "artifact_id": str(candidate.id),
                            "kind": candidate.kind,
                            "title": candidate.title,
                            "media_type": candidate.media_type,
                            "byte_size": candidate.byte_size,
                            "checksum_sha256": candidate.checksum_sha256,
                        },
                    )

            yield public_terminal_from_outcome(
                recorder,
                outcome,
                checkpoint_version=checkpoint_version,
            )
        finally:
            try:
                context.lease.release()
            except Exception:  # noqa: BLE001 - committed outcome cannot be retracted by cleanup
                pass

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
            await self._promote_artifact_candidates(context, outcome.artifact_candidates)
            snapshot = await self._sessions.commit_completed_turn(
                command.session_id,
                user_text=command.message,
                assistant_text=outcome.assistant_text,
                run_id=context.run_id,
                expected_checkpoint_version=args.expected_checkpoint_version,
                artifact_candidates=outcome.artifact_candidates,
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

    @staticmethod
    async def _promote_artifact_candidates(
        context: RLMTurnContext,
        candidates: tuple[ArtifactCandidate, ...],
    ) -> None:
        if not candidates:
            return
        if context.volume_fs is None:
            raise RuntimeError("artifact promotion requires a Workspace Volume Scope")

        for candidate in candidates:
            if (
                candidate.user_id != context.user_id
                or candidate.workspace_id != context.workspace_id
                or candidate.session_id != context.session_id
                or candidate.run_id != context.run_id
            ):
                raise RuntimeError("artifact candidate does not belong to this Turn")
            data = await asyncio.to_thread(context.volume_fs.read_bytes, candidate.staging_path)
            if len(data) != candidate.byte_size:
                raise RuntimeError("artifact candidate size mismatch")
            if hashlib.sha256(data).hexdigest() != candidate.checksum_sha256:
                raise RuntimeError("artifact candidate checksum mismatch")
            await asyncio.to_thread(context.volume_fs.write_bytes, candidate.durable_path, data)


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
