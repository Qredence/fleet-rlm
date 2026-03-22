"""Runtime context snapshot for enriching streaming events.

Captures sandbox, volume, depth, and execution profile metadata from
the agent at turn start.  The snapshot is immutable for the turn
duration and threaded through event-building helpers so every emitted
``StreamEvent`` carries consistent runtime provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent


@dataclass(frozen=True, slots=True)
class StreamingContext:
    """Immutable snapshot of agent runtime metadata for one chat turn.

    Fields
    ------
    depth : int
        Current recursion depth (0 = root agent).
    max_depth : int
        Maximum allowed recursion depth.
    execution_profile : str
        Default execution profile label (e.g. ``"ROOT_INTERLOCUTOR"``).
    volume_name : str | None
        Modal Volume name when persistent storage is attached.
    sandbox_active : bool
        Whether a Modal Sandbox session is currently alive.
    effective_max_iters : int
        Iteration budget computed for this turn.
    execution_mode : str
        High-level execution mode label (for example ``"auto"`` or ``"rlm"``).
    sandbox_id : str | None
        Stable sandbox identifier when the provider exposes one.
    """

    depth: int = 0
    max_depth: int = 2
    execution_profile: str = "ROOT_INTERLOCUTOR"
    volume_name: str | None = None
    sandbox_active: bool = False
    effective_max_iters: int = 10
    execution_mode: str = "auto"
    sandbox_id: str | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_agent(
        cls,
        agent: RLMReActChatAgent,
        *,
        effective_max_iters: int | None = None,
    ) -> StreamingContext:
        """Build a context snapshot from the live agent state."""
        interpreter = agent.interpreter

        profile_name = "ROOT_INTERLOCUTOR"
        if hasattr(interpreter, "default_execution_profile"):
            raw = interpreter.default_execution_profile
            profile_name = str(raw.value) if hasattr(raw, "value") else str(raw)

        return cls(
            depth=agent.current_depth,
            max_depth=agent._max_depth,
            execution_profile=profile_name,
            volume_name=getattr(interpreter, "volume_name", None),
            sandbox_active=getattr(interpreter, "_sandbox", None) is not None,
            effective_max_iters=(
                effective_max_iters
                if effective_max_iters is not None
                else agent._current_effective_max_iters
            ),
            execution_mode=str(getattr(agent, "execution_mode", "auto") or "auto"),
            sandbox_id=None,
        )

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def as_payload(self) -> dict[str, Any]:
        """Return a flat dict suitable for merging into event payloads."""
        ctx: dict[str, Any] = {
            "depth": self.depth,
            "max_depth": self.max_depth,
            "execution_profile": self.execution_profile,
            "sandbox_active": self.sandbox_active,
            "provider_session_active": self.sandbox_active,
            "effective_max_iters": self.effective_max_iters,
            "execution_mode": self.execution_mode,
        }
        if self.volume_name:
            ctx["volume_name"] = self.volume_name
            ctx["configured_volume_name"] = self.volume_name
        if self.sandbox_id:
            ctx["sandbox_id"] = self.sandbox_id
            ctx["provider_session_id"] = self.sandbox_id
        return ctx

    def enrich(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return *payload* merged with runtime context under ``"runtime"``."""
        runtime_payload = self.as_payload()
        existing_runtime = payload.get("runtime")
        if isinstance(existing_runtime, dict):
            runtime_payload = {**runtime_payload, **existing_runtime}

        enriched = dict(payload)
        for key, value in runtime_payload.items():
            enriched.setdefault(key, value)
        enriched["runtime"] = runtime_payload
        return enriched
