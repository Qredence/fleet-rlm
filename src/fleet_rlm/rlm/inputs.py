"""Turn-input rows for the direct-RLM execution path."""

from __future__ import annotations

from typing import Any

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.runtime.events import TurnInputRow


def history_turn_count(agent_runtime: Any | None) -> int:
    """Return the number of history messages on the agent runtime, if present."""
    if agent_runtime is None:
        return 0
    history = getattr(agent_runtime, "history", None)
    if history is None:
        return 0
    return len(getattr(history, "messages", []) or [])


def build_direct_rlm_turn_inputs(
    ctx: ChatExecutionContext,
    message: str,
    agent_runtime: Any | None,
) -> list[TurnInputRow]:
    """Build turn-input rows mirroring the legacy RLM path's assembled inputs."""
    preview_limit = 160
    rows = [
        TurnInputRow(
            label="Request",
            kind="request",
            value=message,
            preview=message[:preview_limit],
        )
    ]

    skills = list(ctx.controls.selected_skill_ids or [])
    if skills:
        preview = ", ".join(skills)
        rows.append(
            TurnInputRow(
                label="Active skills",
                kind="skills",
                value=skills,
                preview=preview[:preview_limit],
            )
        )

    history = getattr(agent_runtime, "history", None) if agent_runtime is not None else None
    if history is not None:
        messages = list(getattr(history, "messages", []) or [])
        rows.append(
            TurnInputRow(
                label="History",
                kind="history",
                value=messages,
                preview=f"{len(messages)} turn(s)",
            )
        )

    core_memory = str(getattr(agent_runtime, "core_memory", "") or "") if agent_runtime is not None else ""
    if core_memory:
        rows.append(
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=core_memory,
                preview=core_memory[:preview_limit],
            )
        )

    return rows


__all__ = ["build_direct_rlm_turn_inputs", "history_turn_count"]
