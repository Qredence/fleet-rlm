"""Native route strategies and their input/output projections, not Turn ownership."""

from typing import Any

import dspy
from dspy.primitives.repl_types import (
    EXTRACT_FALLBACK,
    ExtractFallbackMarker,
    REPLEntry,
    build_repl_event,
    is_repl_event,
    split_repl_event,
)

from fleet_rlm.contracts import Route
from fleet_rlm.engine.signatures import DirectAnswer, FleetTask
from fleet_rlm.observability import activity_text

_MAX_RLM_HISTORY_EVENTS = 16
_MAX_RLM_STORED_TURN_EVENTS = 8
_MAX_RLM_HISTORY_CHARS = 24_000
_MAX_RLM_FIELD_CHARS = 3_000


def _sanitize_repl_parts(
    repl_inputs: dict[str, Any],
    repl_entry: REPLEntry | ExtractFallbackMarker,
    repl_outputs: dict[str, Any],
) -> tuple[dict[str, Any], REPLEntry | ExtractFallbackMarker, dict[str, Any]]:
    bounded_inputs: dict[str, Any] = {}
    if isinstance(repl_inputs.get("task"), str) and repl_inputs["task"]:
        bounded_inputs["task"] = activity_text(repl_inputs["task"], limit=1_000)
    raw_vars = repl_inputs.get("variables_info")
    if isinstance(raw_vars, list):
        bounded_inputs["variables_info"] = [activity_text(str(item), limit=600) for item in raw_vars[:16]]
    if isinstance(repl_inputs.get("iteration"), str):
        bounded_inputs["iteration"] = repl_inputs["iteration"][:64]
    if isinstance(repl_entry, ExtractFallbackMarker):
        bounded_entry: REPLEntry | ExtractFallbackMarker = EXTRACT_FALLBACK
    else:
        bounded_entry = REPLEntry(
            reasoning=activity_text(str(repl_entry.reasoning or ""), limit=1_500),
            code=activity_text(str(repl_entry.code or ""), limit=_MAX_RLM_FIELD_CHARS),
            output=activity_text(str(repl_entry.output or ""), limit=_MAX_RLM_FIELD_CHARS),
            max_output_chars=min(int(repl_entry.max_output_chars), _MAX_RLM_FIELD_CHARS),
        )
    bounded_outputs = {
        str(key)[:64]: activity_text(str(val), limit=_MAX_RLM_FIELD_CHARS)
        for key, val in list(repl_outputs.items())[:8]
        if isinstance(key, str) and val is not None
    }
    return bounded_inputs, bounded_entry, bounded_outputs


def _event_char_cost(event: dict[str, Any]) -> int:
    if not is_repl_event(event):
        return len(str(event))
    repl_inputs, repl_entry, repl_outputs = split_repl_event(event)
    entry_cost = (
        16
        if isinstance(repl_entry, ExtractFallbackMarker)
        else len(repl_entry.reasoning) + len(repl_entry.code) + len(repl_entry.output)
    )
    return (
        sum(len(str(val)) for val in repl_inputs.values())
        + entry_cost
        + sum(len(str(val)) for val in repl_outputs.values())
    )


def rlm_history_processor(history: dspy.History) -> dspy.History:
    """Bound RLM REPL history to recent redacted events without mutating caller inputs."""
    sanitized: list[dict[str, Any]] = []
    for raw in history.messages:
        if not isinstance(raw, dict) or not is_repl_event(raw):
            continue
        repl_inputs, repl_entry, repl_outputs = _sanitize_repl_parts(*split_repl_event(raw))
        sanitized.append(build_repl_event(repl_inputs, repl_entry, repl_outputs))
    kept_reversed: list[dict[str, Any]] = []
    total_chars = 0
    for event in reversed(sanitized[-_MAX_RLM_HISTORY_EVENTS:]):
        cost = _event_char_cost(event)
        if total_chars + cost > _MAX_RLM_HISTORY_CHARS:
            break
        kept_reversed.append(event)
        total_chars += cost
    return dspy.History(messages=list(reversed(kept_reversed)))


def serialize_rlm_turn_history(history: dspy.History | None, *, skip_events: int = 0) -> list[dict[str, Any]]:
    """Serialize one Turn's newly appended RLM REPL events in a JSONB key-order-safe structure."""
    if not isinstance(history, dspy.History) or not history.messages:
        return []
    delta = history.messages[max(0, skip_events) :][-_MAX_RLM_STORED_TURN_EVENTS:]
    rows: list[dict[str, Any]] = []
    for raw in delta:
        if not isinstance(raw, dict) or not is_repl_event(raw):
            continue
        repl_inputs, repl_entry, repl_outputs = _sanitize_repl_parts(*split_repl_event(raw))
        entry_payload: dict[str, Any] = (
            {"kind": "extract_fallback"}
            if isinstance(repl_entry, ExtractFallbackMarker)
            else {
                "kind": "entry",
                "reasoning": repl_entry.reasoning,
                "code": repl_entry.code,
                "output": repl_entry.output,
                "max_output_chars": repl_entry.max_output_chars,
            }
        )
        rows.append({"inputs": repl_inputs, "repl_entry": entry_payload, "outputs": repl_outputs})
    return rows


def deserialize_rlm_history(turns: list[dict[str, Any]]) -> dspy.History | None:
    """Reconstruct bounded read-only RLM history from recent committed Session Turn records."""
    if not isinstance(turns, list):
        return None
    messages: list[dict[str, Any]] = []
    for turn in turns[-4:]:
        if not isinstance(turn, dict):
            continue
        stored = turn.get("rlm_history")
        if not isinstance(stored, list):
            continue
        for row in stored[:_MAX_RLM_STORED_TURN_EVENTS]:
            if not isinstance(row, dict):
                continue
            raw_inputs = row.get("inputs")
            raw_entry = row.get("repl_entry")
            raw_outputs = row.get("outputs")
            if not isinstance(raw_inputs, dict) or not isinstance(raw_entry, dict) or not isinstance(raw_outputs, dict):
                continue
            if raw_entry.get("kind") == "extract_fallback":
                entry: REPLEntry | ExtractFallbackMarker = EXTRACT_FALLBACK
            elif raw_entry.get("kind") == "entry" and isinstance(raw_entry.get("code"), str):
                entry = REPLEntry(
                    reasoning=str(raw_entry.get("reasoning") or ""),
                    code=raw_entry["code"],
                    output=str(raw_entry.get("output") or ""),
                    max_output_chars=min(int(raw_entry.get("max_output_chars") or _MAX_RLM_FIELD_CHARS), 10_000),
                )
            else:
                continue
            messages.append(build_repl_event(raw_inputs, entry, raw_outputs))
    if not messages:
        return None
    processed = rlm_history_processor(dspy.History(messages=messages))
    return processed if processed.messages else None


class Reasoning(dspy.Module):
    """Own the native Direct, RLM and Flex parameters under one execution contract."""

    def __init__(
        self,
        *,
        root_lm: Any,
        sub_lm: Any,
        adapter: dspy.Adapter,
        tools: list[Any],
        rlm_factory: Any,
        flex_factory: Any,
        max_llm_calls: int,
        inline_context: Any,
        emit: Any,
        check_turn: Any,
    ) -> None:
        super().__init__()
        self.direct = dspy.ChainOfThought(DirectAnswer)
        self.rlm = dspy.RLM(
            FleetTask,
            tools=tools,
            sub_lm=sub_lm,
            interpreter_factory=rlm_factory,
            max_iters=16,
            max_llm_calls=max_llm_calls,
            history_processor=rlm_history_processor,
        )
        self.flex = dspy.Flex(
            FleetTask,
            tools=tools,
            interpreter_factory=flex_factory,
            max_predictor_calls=max_llm_calls,
        )
        self._root_lm, self._adapter = root_lm, adapter
        self._inline_context, self._emit, self._check_turn = inline_context, emit, check_turn

    def forward(
        self,
        route: Route,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str,
        session_history: str,
        context: Any,
        mode: str,
        rlm_history: dspy.History | None = None,
    ) -> dspy.Prediction:
        """Project inputs for the selected native solver and normalize task results."""
        if route is Route.DIRECT:
            inlined, chars, truncated = task, 0, False
            if mode == "direct":
                inlined, chars, truncated = self._inline_context(task, workspace_manifest)
                self._emit("direct_context", chars=chars, truncated=truncated)
            with dspy.context(lm=self._root_lm, adapter=self._adapter):
                answer = self.direct(
                    memory_summary=memory_summary, session_history=session_history, task=inlined
                ).answer
            return dspy.Prediction(
                answer=answer,
                learned_skill_note="",
                route=route.value,
                direct_context_chars=chars,
                direct_context_truncated=truncated,
            )

        agent = self.flex if route is Route.FLEX else self.rlm
        prior_rlm = (
            rlm_history_processor(rlm_history)
            if route is Route.RLM and isinstance(rlm_history, dspy.History) and rlm_history.messages
            else None
        )
        call_kwargs: dict[str, Any] = {
            "workspace_manifest": workspace_manifest,
            "skills_catalog": skills_catalog,
            "memory_summary": memory_summary,
            "session_history": session_history,
            "execution_route": route.value,
            "task": task,
            "context": context if context is not None else "",
        }
        if prior_rlm is not None:
            call_kwargs["history"] = prior_rlm
        with dspy.context(lm=self._root_lm, adapter=self._adapter):
            prediction = agent(**call_kwargs)
        self._check_turn()
        extra: dict[str, Any] = {}
        if route is Route.RLM:
            history = getattr(prediction, "history", None)
            if isinstance(history, dspy.History) and history.messages:
                # Native history processors may remove prior events during this call.
                # Trajectory counts only this call's iterations; extraction adds one final marker.
                new_events = len(prediction.trajectory)
                if isinstance(split_repl_event(history.messages[-1])[1], ExtractFallbackMarker):
                    new_events += 1
                turn_events = serialize_rlm_turn_history(
                    history, skip_events=max(0, len(history.messages) - new_events)
                )
                if turn_events:
                    extra["rlm_history"] = turn_events
        return dspy.Prediction(
            answer=prediction.answer,
            learned_skill_note=getattr(prediction, "learned_skill_note", ""),
            route=route.value,
            **extra,
        )
