"""Map normalized RLM trajectory steps to RuntimeEvent objects."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fleet_rlm.runtime.events import EVENT_SCHEMA_VERSION, RuntimeEvent, RuntimeEventKind
from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory


def iter_trajectory_runtime_events(trajectory_raw: Any) -> Iterator[RuntimeEvent]:
    """Yield REASONING / TOOL_CALL / TOOL_RESULT events from an RLM trajectory."""
    trajectory = _normalize_trajectory(trajectory_raw)
    for step in trajectory:
        thought = step.get("thought")
        tool_name = step.get("tool_name")
        is_terminal = (tool_name == "finish") or (not tool_name)
        if thought and not is_terminal:
            reasoning_event = RuntimeEvent.reasoning(str(thought))
            reasoning_event.payload["trajectory_index"] = step.get("index")
            yield reasoning_event

        if tool_name:
            tool_args = step.get("tool_args") or step.get("input", "")
            traj_idx = step.get("index")
            tool_event = RuntimeEvent.tool_call(
                tool_name=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {"input": tool_args},
                step_index=traj_idx,
            )
            tool_event.payload["step"] = step
            tool_event.payload["trajectory_index"] = traj_idx
            yield tool_event

        observation = step.get("observation") or step.get("output", "")
        if observation and tool_name:
            result_event = RuntimeEvent.tool_result(
                tool_name=tool_name,
                observation=observation,
                step_index=step.get("index"),
            )
            result_event.payload["output"] = observation
            result_event.payload["step"] = step
            result_event.payload["trajectory_index"] = step.get("index")
            yield result_event


def build_direct_rlm_done_event(
    *,
    response: str,
    trajectory_raw: Any,
    history_turns: int = 0,
) -> RuntimeEvent:
    """Terminal DONE payload for a completed direct-RLM turn."""
    trajectory = _normalize_trajectory(trajectory_raw)
    return RuntimeEvent(
        kind=RuntimeEventKind.DONE,
        text=response,
        payload={
            "schema_version": EVENT_SCHEMA_VERSION,
            "trajectory": {"steps": trajectory},
            "history_turns": history_turns,
            "execution_backend": "direct_rlm",
        },
    )


__all__ = ["build_direct_rlm_done_event", "iter_trajectory_runtime_events"]
