"""Escalating fleet agent module — ChainOfThought for simple turns, RLM for complex ones.

This module implements the Phase 2 unified agent design: a single DSPy Module
that seamlessly escalates from a lightweight ChainOfThought response to a full
dspy.RLM loop when the situation demands it.

Escalation is triggered when:
- The ChainOfThought reasoning output contains the sentinel ``[TOOLS NEEDED]``.
- The caller sets ``execution_mode="rlm"`` or ``"rlm_only"`` explicitly.
- The caller sets ``force_escalate=True``.
"""

from __future__ import annotations

import logging
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    ConversationSummarySignature,
    RLMReActChatSignature,
)

logger = logging.getLogger(__name__)

ESCALATION_SENTINEL = "[TOOLS NEEDED]"
_RLM_FALLBACK_WARNING = "RLM escalation failed; returned a lightweight fallback response."


def _is_rlm_execution_mode(execution_mode: str) -> bool:
    return execution_mode in {"rlm", "rlm_only"}


def _history_value(message: Any, *keys: str) -> str:
    if isinstance(message, dict):
        for key in keys:
            value = message.get(key)
            if value not in (None, ""):
                return str(value)
    for key in keys:
        value = getattr(message, key, None)
        if value not in (None, ""):
            return str(value)
    return ""


def _format_history_turn(message: Any) -> str:
    user_text = _history_value(message, "user_message", "user_request")
    assistant_text = _history_value(message, "response", "assistant_response", "answer")
    parts: list[str] = []
    if user_text:
        parts.append(f"User: {user_text}")
    if assistant_text:
        parts.append(f"Assistant: {assistant_text}")
    return "\n".join(parts)


class EscalatingFleetModule(dspy.Module):
    """Unified DSPy Module that scales from lightweight chat to full RLM execution.

    Simple turns are handled by a ``dspy.ChainOfThought`` step.  When the
    reasoning contains :data:`ESCALATION_SENTINEL` or the caller requests deep
    work, the module re-runs via an ``RLMVariableExecutionModule`` or a
    raw ``dspy.RLM`` with the same tool set.

    Parameters
    ----------
    interpreter:
        Daytona interpreter instance (may be ``None`` for unit tests).
    tools:
        Tool list to pass to the RLM heavy path.
    max_iterations:
        Maximum RLM iterations for the heavy path.
    max_llm_calls:
        Maximum LLM calls for the heavy path.
    verbose:
        Pass ``verbose=True`` to the inner RLM for debug output.
    sub_lm:
        Optional sub-LM for the RLM heavy path.
    summary_interval:
        How many turns between automatic conversation summary regenerations.
    """

    def __init__(
        self,
        *,
        interpreter: Any | None = None,
        tools: list[Any] | None = None,
        max_iterations: int = 20,
        max_llm_calls: int = 50,
        verbose: bool = False,
        sub_lm: dspy.LM | None = None,
        summary_interval: int = 10,
    ) -> None:
        super().__init__()
        self._interpreter = interpreter
        self._summary_interval = summary_interval
        self._turn_count = 0

        from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule

        volume_mount_path = getattr(interpreter, "volume_mount_path", None) if interpreter else None
        self._skill_selector = SkillSelectionModule(volume_mount_path=volume_mount_path)

        self.respond = dspy.ChainOfThought(RLMReActChatSignature)
        self.summarize = dspy.ChainOfThought(ConversationSummarySignature)

        self._rlm: dspy.Module | None = None
        if interpreter is not None:
            from fleet_rlm.runtime.agent.signatures import RLMVariableSignature
            from fleet_rlm.runtime.modules.variable_mode import build_variable_mode_rlm

            self._rlm = build_variable_mode_rlm(
                signature=RLMVariableSignature,
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                verbose=verbose,
                sub_lm=sub_lm,
                extra_tools=tools or [],
            )
        else:
            self._rlm = dspy.ChainOfThought(RLMReActChatSignature)

    def _should_escalate(
        self,
        prediction: dspy.Prediction,
        *,
        execution_mode: str,
        force_escalate: bool,
    ) -> bool:
        if force_escalate:
            return True
        if _is_rlm_execution_mode(execution_mode):
            return True
        reasoning = str(getattr(prediction, "reasoning", "") or "")
        return ESCALATION_SENTINEL in reasoning

    def compress_history(self, history: dspy.History) -> str:
        """Return a compressed text summary of the given history."""
        messages = list(getattr(history, "messages", []) or [])
        if not messages:
            return ""
        history_text = "\n".join(
            turn_text for turn_text in (_format_history_turn(message) for message in messages) if turn_text
        )
        if not history_text:
            return ""
        try:
            result = self.summarize(conversation_history=history_text)
            return str(getattr(result, "summary", "") or "")
        except Exception as exc:
            logger.warning("Conversation summary failed, returning truncated history: %s", exc)
            return history_text[-4000:]

    def _enrich_with_skills(self, user_request: str, core_memory: str) -> str:
        """Select relevant skills and append their instructions to core_memory."""
        try:
            selection = self._skill_selector(user_request=user_request, core_memory=core_memory)
            skill_context = str(getattr(selection, "skill_context", "") or "")
            if skill_context:
                selected = getattr(selection, "selected_skills", [])
                logger.debug("SkillSelection: injected %s", selected)
                return f"{core_memory}\n\n[Active Skills]\n{skill_context}" if core_memory else skill_context
        except Exception as exc:
            logger.debug("SkillSelection: skipped (%s)", exc)
        return core_memory

    def forward(
        self,
        *,
        user_request: str,
        core_memory: str = "",
        history: dspy.History | None = None,
        execution_mode: str = "auto",
        force_escalate: bool = False,
        conversation_summary: str = "",
    ) -> dspy.Prediction:
        """Run one turn through the escalating module.

        Parameters
        ----------
        user_request:
            The current user message.
        core_memory:
            Serialized core memory string (persona, human, scratchpad blocks).
        history:
            Prior conversation turns as a :class:`dspy.History`.
        execution_mode:
            ``"auto"`` to let the escalation signal decide; ``"rlm"`` to force
            the heavy path; any other value uses the lightweight path.
        force_escalate:
            Bypass the signal check and go directly to RLM.
        conversation_summary:
            Pre-computed session summary; used when ``execution_mode`` is
            ``"rlm"`` and we skip the ChainOfThought step.
        """
        if history is None:
            history = dspy.History(messages=[])

        self._turn_count += 1

        core_memory = self._enrich_with_skills(user_request, core_memory)

        if _is_rlm_execution_mode(execution_mode) or force_escalate:
            logger.debug("EscalatingFleetModule: forced RLM path (mode=%s)", execution_mode)
            return self._run_rlm(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                conversation_summary=conversation_summary,
            )

        prediction = self.respond(
            user_request=user_request,
            core_memory=core_memory,
            history=history,
        )

        if self._should_escalate(prediction, execution_mode=execution_mode, force_escalate=False):
            logger.debug("EscalatingFleetModule: escalating to RLM (sentinel found in reasoning)")
            return self._run_rlm(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                conversation_summary=conversation_summary,
            )

        return prediction

    def _run_rlm(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
        conversation_summary: str,
    ) -> dspy.Prediction:
        if self._rlm is None:
            return self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
            )
        context = conversation_summary or self.compress_history(history)
        try:
            return self._rlm(
                task=user_request,
                prompt=context or core_memory or user_request,
            )
        except Exception as exc:
            logger.warning("EscalatingFleetModule: RLM path failed (%s), falling back to ChainOfThought", exc)
            fallback = self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
            )
            fallback["degraded"] = True
            fallback["warning"] = _RLM_FALLBACK_WARNING
            fallback["runtime_degraded"] = True
            fallback["runtime_failure_category"] = "rlm_fallback"
            fallback["runtime_failure_phase"] = "escalating_rlm"
            fallback["runtime_fallback_used"] = True
            fallback["runtime_warning"] = _RLM_FALLBACK_WARNING
            return fallback


__all__ = [
    "ESCALATION_SENTINEL",
    "EscalatingFleetModule",
]
