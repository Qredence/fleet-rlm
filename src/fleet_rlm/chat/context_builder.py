"""Turn context construction seam (offline + rebind helpers).

Production modules must not import ``unittest.mock``. Offline adapters use
explicit stubs so tests and kernel smoke stay free of DSPy/Daytona clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import InterpreterLeaseLike, RLMTurnContext
from fleet_rlm.rlm.model_bundle import RLMModelBundle


class TurnContextBuilder(Protocol):
    """External seam: command → full RLMTurnContext (may acquire resources)."""

    def build(self, command: ChatTurnCommand) -> RLMTurnContext: ...


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


def ephemeral_lease(interpreter: Any) -> InterpreterLeaseLike:
    """Public helper for tests and offline wiring."""
    return _EphemeralLease(interpreter)


class OfflineLM:
    """Placeholder LM role object for offline/kernel paths (not a live dspy.LM)."""

    def __init__(self, name: str = "offline_lm") -> None:
        self.name = name


class OfflineInterpreter:
    """No-op interpreter for offline context construction without Daytona."""

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> str:
        del code, variables
        return ""

    def shutdown(self) -> None:
        return None


@dataclass
class OfflineContextBuilder:
    """Deep-enough offline adapter: builds a complete context without providers."""

    budget: RLMBudget = field(default_factory=RLMBudget)

    def build(self, command: ChatTurnCommand) -> RLMTurnContext:
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=RLMModelBundle(
                root_lm=OfflineLM("root_lm"),
                sub_lm=OfflineLM("sub_lm"),
            ),
            budget=self.budget,
            lease=ephemeral_lease(OfflineInterpreter()),
        )


def rebind_turn_context(
    context: RLMTurnContext,
    *,
    run_id: UUID | None = None,
    history: Any = ...,
    session_summary: str | None = None,
    cancel_probe: Any = ...,
) -> RLMTurnContext:
    """Return a new context with claim/session fields applied.

    Hides field-by-field copy from TurnCoordinator so construction stays local.
    Pass ``history`` explicitly (including ``None``) to override; omit to keep.
    """
    resolved_history = context.history if history is ... else history
    resolved_probe = context.cancel_probe if cancel_probe is ... else cancel_probe
    return RLMTurnContext(
        run_id=run_id if run_id is not None else context.run_id,
        session_id=context.session_id,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        request=context.request,
        models=context.models,
        budget=context.budget,
        lease=context.lease,
        history=resolved_history,
        session_summary=(session_summary if session_summary is not None else context.session_summary),
        skill_cards=context.skill_cards,
        attachments=context.attachments,
        artifacts=context.artifacts,
        tools=context.tools,
        skill_tool_host=getattr(context, "skill_tool_host", None),
        file_tool_host=getattr(context, "file_tool_host", None),
        volume_fs=getattr(context, "volume_fs", None),
        cancel_probe=resolved_probe,
    )
