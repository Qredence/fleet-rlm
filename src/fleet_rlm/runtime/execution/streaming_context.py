"""Immutable streaming context snapshot for RLM ReAct chat turns.

Captures agent runtime metadata (depth, execution profile, sandbox state,
volume info, degradation flags) as a frozen dataclass for one chat turn.
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
    workspace_path : str | None
        Active sandbox workspace path when the provider exposes one.
    sandbox_transition : str | None
        Lifecycle transition for the active provider session (for example
        ``"created"``, ``"reused"``, or ``"resumed"``).
    runtime_degraded : bool
        Whether the active runtime degraded and the turn recovered via fallback.
    runtime_failure_category : str | None
        Stable failure category for the primary runtime error, when available.
    runtime_failure_phase : str | None
        Stable failure phase for the primary runtime error, when available.
    runtime_fallback_used : bool
        Whether the turn recovered via a runtime fallback after degradation.
    """

    depth: int = 0
    max_depth: int = 2
    execution_profile: str = "ROOT_INTERLOCUTOR"
    volume_name: str | None = None
    sandbox_active: bool = False
    effective_max_iters: int = 10
    execution_mode: str = "auto"
    sandbox_id: str | None = None
    workspace_path: str | None = None
    sandbox_transition: str | None = None
    runtime_degraded: bool = False
    runtime_failure_category: str | None = None
    runtime_failure_phase: str | None = None
    runtime_fallback_used: bool = False

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
        runtime_metadata_fn = getattr(interpreter, "current_runtime_metadata", None)
        runtime_metadata = (
            runtime_metadata_fn() if callable(runtime_metadata_fn) else {}
        )
        session = getattr(interpreter, "_session", None)
        fallback_sandbox_active = (
            session is not None or getattr(interpreter, "_sandbox", None) is not None
        )
        sandbox_active = bool(
            runtime_metadata.get("sandbox_active", fallback_sandbox_active)
            if isinstance(runtime_metadata, dict)
            else fallback_sandbox_active
        )

        sandbox_id = None
        workspace_path = None
        sandbox_transition = None
        volume_name = getattr(interpreter, "volume_name", None)
        runtime_degraded = False
        runtime_failure_category = None
        runtime_failure_phase = None
        runtime_fallback_used = False
        if isinstance(runtime_metadata, dict):
            sandbox_id = runtime_metadata.get("sandbox_id")
            workspace_path = runtime_metadata.get("workspace_path")
            sandbox_transition = runtime_metadata.get("sandbox_transition")
            volume_name = runtime_metadata.get("volume_name", volume_name)
            runtime_degraded = bool(runtime_metadata.get("runtime_degraded", False))
            runtime_failure_category = runtime_metadata.get("runtime_failure_category")
            runtime_failure_phase = runtime_metadata.get("runtime_failure_phase")
            runtime_fallback_used = bool(
                runtime_metadata.get("runtime_fallback_used", False)
            )

        profile_name = "ROOT_INTERLOCUTOR"
        if hasattr(interpreter, "default_execution_profile"):
            raw = interpreter.default_execution_profile
            profile_name = str(raw.value) if hasattr(raw, "value") else str(raw)

        return cls(
            depth=agent.current_depth,
            max_depth=agent._max_depth,
            execution_profile=profile_name,
            volume_name=volume_name,
            sandbox_active=sandbox_active,
            effective_max_iters=(
                effective_max_iters
                if effective_max_iters is not None
                else agent._turn_delegation_state.effective_max_iters
            ),
            execution_mode=str(getattr(agent, "execution_mode", "auto") or "auto"),
            sandbox_id=str(sandbox_id).strip() or None if sandbox_id else None,
            workspace_path=(
                str(workspace_path).strip() or None if workspace_path else None
            ),
            sandbox_transition=(
                str(sandbox_transition).strip() or None if sandbox_transition else None
            ),
            runtime_degraded=runtime_degraded,
            runtime_failure_category=(
                str(runtime_failure_category).strip() or None
                if runtime_failure_category
                else None
            ),
            runtime_failure_phase=(
                str(runtime_failure_phase).strip() or None
                if runtime_failure_phase
                else None
            ),
            runtime_fallback_used=runtime_fallback_used,
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
        if self.workspace_path:
            ctx["workspace_path"] = self.workspace_path
        if self.sandbox_transition:
            ctx["sandbox_transition"] = self.sandbox_transition
        if self.runtime_degraded:
            ctx["runtime_degraded"] = True
        if self.runtime_failure_category:
            ctx["runtime_failure_category"] = self.runtime_failure_category
        if self.runtime_failure_phase:
            ctx["runtime_failure_phase"] = self.runtime_failure_phase
        if self.runtime_fallback_used:
            ctx["runtime_fallback_used"] = True
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
