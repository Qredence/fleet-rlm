"""Pure payload extractors for execution step building."""

from __future__ import annotations

from typing import Any, Literal

ExecutionStepType = Literal["llm", "tool", "repl", "memory", "output"]
ExecutionActorKind = Literal["root_rlm", "sub_agent", "delegate", "unknown"]


def _extract_depth(payload: dict[str, Any]) -> int | None:
    candidates: list[Any] = [
        payload.get("depth"),
        payload.get("delegate_depth"),
        payload.get("sub_agent_depth"),
    ]
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        candidates.extend(
            [
                step_data.get("depth"),
                step_data.get("delegate_depth"),
                step_data.get("sub_agent_depth"),
            ]
        )
    for raw in candidates:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return max(0, int(raw))
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def _extract_parent_hint(payload: dict[str, Any]) -> str | None:
    for key in ("parent_step_id", "parent_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        for key in ("parent_step_id", "parent_id"):
            value = step_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _iter_actor_sources(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        return (payload, step_data)
    return (payload,)


def _extract_actor_id(payload: dict[str, Any]) -> str | None:
    for source in _iter_actor_sources(payload):
        for key in ("actor_id", "delegate_id", "sub_agent_id", "agent_id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _map_actor_kind_text(value: str) -> ExecutionActorKind | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if lowered in {"root", "root_rlm", "root-rlm", "root agent"}:
        return "root_rlm"
    if lowered in {"sub_agent", "sub-agent", "subagent"}:
        return "sub_agent"
    if lowered in {"delegate", "rlm_delegate", "rlm-delegate"}:
        return "delegate"
    return None


def _extract_actor_kind_from_text(payload: dict[str, Any]) -> ExecutionActorKind | None:
    for source in _iter_actor_sources(payload):
        for key in ("actor_kind", "actor", "agent_kind", "agent_role"):
            value = source.get(key)
            if not isinstance(value, str):
                continue
            mapped = _map_actor_kind_text(value)
            if mapped is not None:
                return mapped
    return None


def _is_delegate_execution_profile(payload: dict[str, Any]) -> bool:
    execution_profile = str(payload.get("execution_profile", "")).strip().upper()
    return execution_profile == "RLM_DELEGATE"


def _has_actor_marker(source: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _extract_actor_kind_from_markers(
    payload: dict[str, Any],
) -> ExecutionActorKind | None:
    for source in _iter_actor_sources(payload):
        if _has_actor_marker(source, ("delegate_depth", "delegate_id")):
            return "delegate"
        if _has_actor_marker(source, ("sub_agent_depth", "sub_agent_id")):
            return "sub_agent"
    return None


def _actor_kind_from_depth(depth: int | None) -> ExecutionActorKind:
    if depth is None:
        return "unknown"
    return "sub_agent" if depth > 0 else "root_rlm"


def _extract_actor_kind(
    payload: dict[str, Any],
    *,
    depth: int | None,
) -> ExecutionActorKind:
    mapped = _extract_actor_kind_from_text(payload)
    if mapped is not None:
        return mapped

    if _is_delegate_execution_profile(payload):
        return "delegate"

    marker_kind = _extract_actor_kind_from_markers(payload)
    if marker_kind is not None:
        return marker_kind

    return _actor_kind_from_depth(depth)


def _derive_lane_key(
    actor_kind: ExecutionActorKind,
    actor_id: str | None,
    depth: int | None,
) -> str:
    if actor_id:
        return f"{actor_kind}:{actor_id}"
    if depth is not None:
        return f"{actor_kind}:depth-{depth}"
    return actor_kind


def _extract_tool_name(text: str, payload: dict[str, Any]) -> str | None:
    raw_name = payload.get("tool_name")
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()

    status_message = str(payload.get("raw_status", text or "")).strip()
    if status_message.startswith("Calling tool:"):
        return status_message.removeprefix("Calling tool:").strip().split("(", 1)[0]
    if text.startswith("tool call:"):
        return text.removeprefix("tool call:").strip().split("(", 1)[0]
    return None


def _tool_step_type(tool_name: str | None) -> ExecutionStepType:
    if not tool_name:
        return "tool"
    lowered = tool_name.lower()
    if lowered.startswith("memory_") or lowered.startswith("core_memory_"):
        return "memory"
    return "tool"
