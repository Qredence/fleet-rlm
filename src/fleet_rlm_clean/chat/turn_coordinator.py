"""Thin use-case: assemble turn context and stream RuntimeEvents from RLMRunner."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol
from unittest.mock import MagicMock
from uuid import uuid4

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import InterpreterLeaseLike, RLMTurnContext
from fleet_rlm_clean.rlm.events import RuntimeEvent
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle
from fleet_rlm_clean.rlm.runner import RLMRunner


class TurnRunner(Protocol):
    def stream(self, context: RLMTurnContext) -> AsyncIterator[RuntimeEvent]: ...


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
    ) -> None:
        self._runner: TurnRunner = runner if runner is not None else RLMRunner()
        self._context_builder = (
            context_builder if context_builder is not None else _default_kernel_context
        )

    async def stream(self, command: ChatTurnCommand) -> AsyncIterator[RuntimeEvent]:
        """Stream public RuntimeEvents for a single chat command."""
        if not command.message or not command.message.strip():
            msg = "message is required"
            raise ValueError(msg)
        context = self._context_builder(command)
        async for event in self._runner.stream(context):
            yield event


def ephemeral_lease(interpreter: Any) -> InterpreterLeaseLike:
    """Public helper for tests and kernel wiring."""
    return _EphemeralLease(interpreter)
