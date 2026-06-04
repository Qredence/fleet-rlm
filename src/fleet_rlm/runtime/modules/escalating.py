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
import re
from dataclasses import dataclass, field
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    ConversationSummarySignature,
    RLMReActChatSignature,
)

logger = logging.getLogger(__name__)

ESCALATION_SENTINEL = "[TOOLS NEEDED]"
_RLM_FALLBACK_WARNING = "RLM escalation failed; returned a lightweight fallback response."
_URL_DOCUMENT_MAX_ITERATIONS = 4
_URL_DOCUMENT_MAX_LLM_CALLS = 8
_URL_RE = re.compile(r"https?://[^\s)\],;]+", flags=re.IGNORECASE)
_URL_DOCUMENT_ANALYSIS_TERMS = (
    "analyze",
    "analyse",
    "analysis",
    "summarize",
    "summarise",
    "summary",
    "read",
    "documentation",
    "docs",
    "document",
    "page",
)
_RLM_REPL_GUIDANCE = """RLM REPL guidance:
- Keep the task visible: solve the task stated at the top and repeated at the bottom of this prompt.
- Use Python variables instead of printing large inputs. Inspect slices, lengths, keywords, and structure with code.
- Treat available tools as ordinary Python callables. Their type hints and docstrings are the contract.
- For documentation URLs, first inspect headings, links, llms.txt, sitemap entries, and section samples with Python. Do not send an entire document to one semantic callback.
- If semantic callbacks such as llm_query are unavailable, finish from Python document inspection.
- Keep intermediate output bounded; print summaries or small samples, then call SUBMIT(...) for the final answer.
- Do not print or return credentials, environment variables, or hidden configuration values.
"""


@dataclass(slots=True)
class _FetchedUrlDocument:
    """Fetched URL document payload passed into DSPy RLM as REPL variables."""

    source_url: str
    document_text: str = ""
    source_metadata: dict[str, str] = field(default_factory=dict)


def _is_rlm_execution_mode(execution_mode: str) -> bool:
    return execution_mode in {"rlm", "rlm_only"}


def _extract_first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,;]") if match else None


def _is_url_document_analysis_request(text: str) -> bool:
    if _extract_first_url(text) is None:
        return False
    lowered = text.lower()
    return any(term in lowered for term in _URL_DOCUMENT_ANALYSIS_TERMS)


def _prediction_set(prediction: dspy.Prediction, key: str, value: Any) -> None:
    try:
        prediction[key] = value
    except Exception:
        object.__setattr__(prediction, key, value)


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


def _format_recent_history_context(history: dspy.History, *, max_turns: int = 4) -> str:
    """Return an explicit recency-ordered history view for model inputs."""
    messages = list(getattr(history, "messages", []) or [])
    if not messages:
        return ""

    recent = messages[-max_turns:]
    lines = [
        "Recent chat history, ordered oldest to newest.",
        "The final listed turn is the most recent prior user/assistant exchange.",
    ]
    for index, message in enumerate(recent, start=1):
        turn = _format_history_turn(message)
        if not turn:
            continue
        marker = "most recent prior turn" if index == len(recent) else f"prior turn {index}"
        lines.append(f"[{marker}]")
        lines.append(turn)
    return "\n".join(lines)


def _build_rlm_prompt_context(
    *,
    user_request: str,
    recent_history: str,
    compressed_history: str,
    core_memory: str,
    url_document_mode: bool,
) -> str:
    """Build a Fast-RLM-style prompt envelope for variable-mode DSPy RLM."""
    sections = [
        "Task:\n" + user_request,
        _RLM_REPL_GUIDANCE,
    ]
    if url_document_mode:
        sections.append(
            "URL document variables:\n"
            "- source_url: canonical fetched URL string.\n"
            "- document_text: extracted source text; inspect it with Python rather than printing it wholesale.\n"
            "- source_metadata: fetch status, source metadata, and any bundled llms.txt/sitemap companions.\n"
            "- llm_query and llm_query_batched are disabled in this URL-document path; synthesize from Python inspection.\n"
            "- history: structured dspy.History for prior turns."
        )
    if recent_history:
        sections.append(recent_history)
    if compressed_history:
        sections.append("Compressed conversation context:\n" + compressed_history)
    if core_memory:
        sections.append("Core memory and active skill guidance:\n" + core_memory)
    sections.append("Repeat task:\n" + user_request)
    return "\n\n".join(section for section in sections if section.strip())


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
    max_output_chars:
        Maximum REPL output characters exposed back to the RLM per step.
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
        max_output_chars: int | None = None,
        verbose: bool = False,
        sub_lm: dspy.LM | None = None,
        summary_interval: int = 10,
    ) -> None:
        super().__init__()
        self._interpreter = interpreter
        self._summary_interval = summary_interval
        self._turn_count = 0

        from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule
        from fleet_rlm.runtime.tools._volume_paths import volume_root

        volume_mount_path = getattr(interpreter, "volume_mount_path", None) if interpreter is not None else None
        if volume_mount_path is None:
            resolved = volume_root()
            volume_mount_path = str(resolved) if resolved is not None else None
        self._skill_selector = SkillSelectionModule(volume_mount_path=volume_mount_path)

        self.respond = dspy.ChainOfThought(RLMReActChatSignature)
        self.summarize = dspy.ChainOfThought(ConversationSummarySignature)

        self._rlm: dspy.Module | None = None
        self._url_document_rlm: dspy.Module | None = None
        if interpreter is not None:
            from fleet_rlm.runtime.agent.signatures import RLMLargeDocSignature, RLMVariableSignature
            from fleet_rlm.runtime.modules.variable_mode import build_variable_mode_rlm

            self._rlm = build_variable_mode_rlm(
                signature=RLMVariableSignature,
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                max_output_chars=max_output_chars,
                verbose=verbose,
                sub_lm=sub_lm,
                extra_tools=tools or [],
            )
            self._url_document_rlm = build_variable_mode_rlm(
                signature=RLMLargeDocSignature,
                interpreter=interpreter,
                max_iterations=max(1, min(max_iterations, _URL_DOCUMENT_MAX_ITERATIONS)),
                max_llm_calls=max(1, min(max_llm_calls, _URL_DOCUMENT_MAX_LLM_CALLS)),
                max_output_chars=max_output_chars,
                verbose=verbose,
                sub_lm=sub_lm,
                extra_tools=[],
                include_sub_tools=False,
                include_llm_tools=False,
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

    def preview_routing(self, *, user_request: str, execution_mode: str = "auto") -> dict[str, Any]:
        """Return deterministic routing metadata available before the full turn."""
        if execution_mode == "auto" and _is_url_document_analysis_request(user_request):
            source_url = _extract_first_url(user_request)
            payload: dict[str, Any] = {"routing_decision": "url_document_rlm"}
            if source_url:
                payload["source_url"] = source_url
            return payload
        if _is_rlm_execution_mode(execution_mode):
            return {"routing_decision": "forced_rlm"}
        return {}

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

    def _enrich_with_skills(self, user_request: str, core_memory: str) -> tuple[str, list[str]]:
        """Select relevant skills and append their instructions to core_memory."""
        try:
            selection = self._skill_selector(user_request=user_request, core_memory=core_memory)
            skill_context = str(getattr(selection, "skill_context", "") or "")
            selected = [str(item) for item in list(getattr(selection, "selected_skills", []) or [])]
            if skill_context:
                logger.debug("SkillSelection: injected %s", selected)
                enriched = f"{core_memory}\n\n[Active Skills]\n{skill_context}" if core_memory else skill_context
                return enriched, selected
            return core_memory, selected
        except Exception as exc:
            logger.debug("SkillSelection: skipped (%s)", exc)
        return core_memory, []

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

        core_memory, selected_skills = self._enrich_with_skills(user_request, core_memory)
        recent_history = _format_recent_history_context(history)
        should_auto_route_url = execution_mode == "auto" and _is_url_document_analysis_request(user_request)
        source_url = _extract_first_url(user_request) if should_auto_route_url else None

        if _is_rlm_execution_mode(execution_mode) or force_escalate or should_auto_route_url:
            logger.debug("EscalatingFleetModule: forced RLM path (mode=%s)", execution_mode)
            return self._run_rlm(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                recent_history=recent_history,
                conversation_summary=conversation_summary,
                selected_skills=selected_skills,
                routing_decision="url_document_rlm" if should_auto_route_url else "forced_rlm",
                source_url=source_url,
            )

        prediction = self.respond(
            user_request=user_request,
            core_memory=core_memory,
            history=history,
            recent_history=recent_history,
        )

        if self._should_escalate(prediction, execution_mode=execution_mode, force_escalate=False):
            logger.debug("EscalatingFleetModule: escalating to RLM (sentinel found in reasoning)")
            return self._run_rlm(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                recent_history=recent_history,
                conversation_summary=conversation_summary,
                selected_skills=selected_skills,
                routing_decision="sentinel_rlm",
                source_url=None,
            )

        _prediction_set(prediction, "selected_skills", selected_skills)
        return prediction

    def _run_rlm(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
        recent_history: str,
        conversation_summary: str,
        selected_skills: list[str] | None = None,
        routing_decision: str = "rlm",
        source_url: str | None = None,
    ) -> dspy.Prediction:
        if self._rlm is None:
            return self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                recent_history=recent_history,
            )
        context = conversation_summary or self.compress_history(history)
        rlm = self._url_document_rlm if source_url and self._url_document_rlm is not None else self._rlm
        url_document_mode = bool(source_url and rlm is self._url_document_rlm)
        prompt = _build_rlm_prompt_context(
            user_request=user_request,
            recent_history=recent_history,
            compressed_history=context,
            core_memory=core_memory,
            url_document_mode=url_document_mode,
        )
        call_kwargs: dict[str, Any] = {
            "task": user_request,
            "prompt": prompt,
            # Phase 7: expose structured history as a native REPL variable on
            # the heavy RLM path (both RLMVariableSignature and
            # RLMLargeDocSignature declare a ``history`` input field), so the
            # model can inspect full prior turns with code rather than relying
            # solely on the flattened recency snippet embedded in ``prompt``.
            "history": history,
        }
        if url_document_mode:
            fetched = self._fetch_url_document(source_url=source_url)
            call_kwargs["source_url"] = fetched.source_url
            call_kwargs["document_text"] = fetched.document_text
            call_kwargs["source_metadata"] = fetched.source_metadata
        try:
            result = rlm(**call_kwargs)
            _prediction_set(result, "selected_skills", selected_skills or [])
            _prediction_set(result, "routing_decision", routing_decision)
            if source_url:
                _prediction_set(result, "source_url", source_url)
            return result
        except Exception as exc:
            logger.warning("EscalatingFleetModule: RLM path failed (%s), falling back to ChainOfThought", exc)
            fallback = self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                recent_history=recent_history,
            )
            fallback["degraded"] = True
            fallback["warning"] = _RLM_FALLBACK_WARNING
            fallback["runtime_degraded"] = True
            fallback["runtime_failure_category"] = "rlm_fallback"
            fallback["runtime_failure_phase"] = "escalating_rlm"
            fallback["runtime_fallback_used"] = True
            fallback["runtime_warning"] = _RLM_FALLBACK_WARNING
            fallback["selected_skills"] = selected_skills or []
            fallback["routing_decision"] = routing_decision
            if source_url:
                fallback["source_url"] = source_url
            return fallback

    def _fetch_url_document(self, *, source_url: str) -> _FetchedUrlDocument:
        if self._interpreter is None:
            return _FetchedUrlDocument(
                source_url=source_url,
                source_metadata={"status": "not_fetched", "reason": "interpreter_unavailable"},
            )
        try:
            from fleet_rlm.runtime.tools.document_tools import fetch_document_text

            fetched = fetch_document_text(source_url)
        except Exception as exc:
            return _FetchedUrlDocument(
                source_url=source_url,
                source_metadata={"status": "error", "error": str(exc)},
            )

        if fetched.get("status") != "ok":
            return _FetchedUrlDocument(
                source_url=source_url,
                source_metadata={
                    "status": "error",
                    "error": str(fetched.get("error", "unknown error")),
                },
            )

        text = str(fetched.get("text") or "")
        char_count = fetched.get("char_count", len(text))
        raw_metadata = fetched.get("metadata")
        metadata: dict[str, str] = {
            "status": "ok",
            "char_count": str(char_count),
        }
        if isinstance(raw_metadata, dict):
            metadata.update({str(key): str(value) for key, value in raw_metadata.items()})
        return _FetchedUrlDocument(
            source_url=source_url,
            document_text=text,
            source_metadata=metadata,
        )


__all__ = [
    "ESCALATION_SENTINEL",
    "EscalatingFleetModule",
]
