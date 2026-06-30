"""Fleet program — typed per-turn routing across direct, ReAct tools, and RLM paths.

This module implements the unified agent design: a single DSPy Module that
routes each chat turn through a typed ``dspy.Predict`` router instead of a
free-text sentinel.

Routing:
- Deterministic signals (``execution_mode="rlm"``/``"rlm_only"``,
  ``force_escalate=True``, URL-document requests, oversized turn context) go
  straight to the ``dspy.RLM`` sandbox via :func:`resolve_rlm_routing`.
- All other turns are classified by :class:`RouteTurnSignature` into
  ``direct`` (ChainOfThought), ``tools`` (the shared ``dspy.ReAct`` loop), or
  ``rlm`` (sandboxed Python execution).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import (
    ConversationSummarySignature,
    RLMDocumentTurnSignature,
    RLMReActChatSignature,
    RLMTurnSignature,
    RLMWorkspaceTurnSignature,
    RouteTurnSignature,
)
from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.events import RuntimeEvent, TurnInputRow
from fleet_rlm.runtime.modules.factory import (
    VARIABLE_MODE_MAX_OUTPUT_CHARS,
    create_runtime_rlm,
    interpreter_delegation_tools,
)
from fleet_rlm.runtime.modules.rlm_prompts import build_rlm_core_context, url_repl_only_enabled
from fleet_rlm.runtime.modules.rlm_routing import (
    fetch_url_document,
    resolve_rlm_routing,
)
from fleet_rlm.runtime.sandbox_types import ActiveSkills, LargeDocument, WorkspaceContext

logger = logging.getLogger(__name__)

_RLM_FALLBACK_WARNING = "RLM escalation failed; returned a lightweight fallback response."
_REACT_FALLBACK_WARNING = "ReAct tool loop failed; returned a lightweight fallback response."
_REACT_MAX_ITERS = 10


def _env_int(name: str, default: int) -> int:
    """Read an integer from environment variable, returning default if not set or invalid."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


_URL_DOCUMENT_MAX_ITERATIONS = _env_int("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", 12)
_URL_DOCUMENT_MAX_LLM_CALLS = _env_int("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", 30)

# Deadline for the escalation fallback (``self.respond``) and the corrective
# RLM retries inside ``_run_rlm``. Independent from ``FLEET_RLM_ACTION_TIMEOUT``
# (which bounds a single action-generation iteration) because the fallback is a
# full ChainOfThought response. Prevents a degraded model from holding a turn
# for tens of minutes via litellm/tenacity retries.
_FALLBACK_TIMEOUT = _env_int("FLEET_RLM_FALLBACK_TIMEOUT", 90)


def _run_with_timeout(fn: Callable[..., Any], *, timeout: int) -> Any:
    """Run *fn* in a worker thread with contextvar propagation and a deadline.

    Returns the callable's result on success, raises
    :class:`concurrent.futures.TimeoutError` on timeout, and re-raises any
    other exception unchanged.

    ``contextvars.copy_context().run`` is required because the callables
    passed here (``self.respond``, ``rlm(...)``) read ``dspy.settings.lm`` —
    a per-session override that a raw ``ThreadPoolExecutor`` worker would not
    see.

    On timeout the worker thread is abandoned (Python cannot force-kill it);
    ``shutdown(wait=False, cancel_futures=True)`` ensures this function
    returns immediately instead of blocking in ``__exit__`` for the orphaned
    worker (which would defeat the deadline — the original 21-minute hang).

    Partial result preservation: if the worker produces a result before timeout
    is detected (race condition), we attempt to capture it via a shared list.
    """
    partial_result: list[Any] = []

    def _capture_and_run():
        result = fn()
        partial_result.append(result)
        return result

    ctx = contextvars.copy_context()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(ctx.run, _capture_and_run)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        # Check if worker produced a partial result before timeout was detected
        if partial_result:
            logger.info(
                "_run_with_timeout: timeout fired but worker produced result (race condition). "
                "Returning partial result instead of timing out."
            )
            return partial_result[0]
        raise
    finally:
        # Don't wait for a runaway worker; cancel pending work and return.
        executor.shutdown(wait=False, cancel_futures=True)


def _is_rlm_parse_error(exc: Exception) -> bool:
    """Return True if the exception indicates a JSON/parse error from RLM extraction.

    Narrowed to JSON-specific markers and ``json.JSONDecodeError`` so that
    broad substrings like ``"expected"``, ``"invalid"``, ``"decode"``, or
    ``"extraction"`` in isolation (e.g. ``ValueError("Invalid API key")`` or
    ``RuntimeError("extraction failed")``) do NOT trigger the parse-error
    retry path. Only genuine JSON parse failures (containing ``"json"``,
    ``"json.decode"``, ``"json parse"``, ``"malformed json"``,
    ``"parse error"``) or ``json.JSONDecodeError`` instances are classified
    as parse errors.
    """
    import json

    if isinstance(exc, json.JSONDecodeError):
        return True
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "json",
            "json.decode",
            "json parse",
            "malformed json",
            "parse error",
        )
    )


def _is_malformed_rlm_result(result: Any) -> bool:
    """Check if RLM result is a known malformed non-JSON-object pattern."""
    if isinstance(result, str):
        stripped = result.strip()
        if stripped in ("[[ ]]", "[]", "[1]", "[0]") or stripped.startswith("[["):
            return True
    elif isinstance(result, dspy.Prediction):
        # Check if the prediction has empty/minimal content
        answer = str(getattr(result, "answer", "") or getattr(result, "response", "") or "").strip()
        if answer in ("[[ ]]", "[]", "[1]", "[0]") or answer.startswith("[["):
            return True
    return False


def _prediction_set(prediction: dspy.Prediction, key: str, value: Any) -> None:
    try:
        prediction[key] = value
    except Exception:
        object.__setattr__(prediction, key, value)


def _history_turn_text(message: Any) -> str:
    if isinstance(message, dict):
        user_text = str(message.get("user_message") or "")
        assistant_text = str(message.get("response") or "")
    else:
        user_text = str(getattr(message, "user_message", "") or "")
        assistant_text = str(getattr(message, "response", "") or "")
    parts: list[str] = []
    if user_text:
        parts.append(f"User: {user_text}")
    if assistant_text:
        parts.append(f"Assistant: {assistant_text}")
    return "\n".join(parts)


async def _to_thread_unstreamed(fn: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Run blocking work in a worker thread with dspy token streaming disabled.

    Under ``dspy.streamify`` the settings carry a ``send_stream``; sync LM
    calls then try to forward chunks via anyio's thread portal, which raises
    from plain ``asyncio.to_thread`` workers. Clearing the stream keeps
    worker-thread predictors on the regular non-streaming path.
    """
    with dspy.context(send_stream=None, stream_listeners=[]):
        return await asyncio.to_thread(fn, **kwargs)


def _describe_tools(tools: list[Any]) -> str:
    """Render a one-line-per-tool inventory for the routing signature."""
    lines: list[str] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or "")
        if not name:
            continue
        desc = str(getattr(tool, "desc", "") or getattr(tool, "__doc__", "") or "").strip()
        first_line = desc.splitlines()[0] if desc else ""
        lines.append(f"- {name}: {first_line}" if first_line else f"- {name}")
    return "\n".join(lines)


def _emit_turn_milestone(interpreter: Any | None, *, phase: str, text: str, **extra: Any) -> None:
    if interpreter is None:
        return
    callback = getattr(interpreter, "_turn_step_callback", None)
    if not callable(callback):
        return
    payload = {"phase": phase, "text": text, **extra}
    try:
        callback(payload)
    except Exception:
        return


def _preview_text(value: Any, max_chars: int = 120) -> str:
    """Return a single-line preview of an arbitrary value."""
    text = str(value or "").strip()
    if not text:
        return ""
    first_line = text.split("\n", 1)[0]
    if len(first_line) > max_chars:
        return first_line[:max_chars] + "..."
    return first_line


def _history_preview(history: dspy.History) -> str:
    """Return a short preview of the conversation history."""
    messages = list(getattr(history, "messages", []) or [])
    count = len(messages)
    if count == 0:
        return "No prior history"
    return f"{count} turn{'s' if count != 1 else ''}"


def _emit_turn_inputs(
    interpreter: Any | None,
    rows: list[TurnInputRow],
    *,
    module: EscalatingFleetModule,
) -> None:
    """Emit a TURN_INPUTS RuntimeEvent through the progress relay, once per turn.

    Uses a per-turn flag on *module* to prevent re-emission on retry/fallback.
    """
    if getattr(module, "_turn_inputs_emitted", False):
        return
    module._turn_inputs_emitted = True
    if interpreter is None:
        return
    relay = getattr(interpreter, "_turn_progress_relay", None)
    if relay is None:
        return
    event = RuntimeEvent.turn_inputs(rows)
    emit_fn = getattr(relay, "emit_threadsafe", None)
    if callable(emit_fn):
        try:
            emit_fn(event)
        except Exception:
            logger.debug("Failed to emit turn_inputs event", exc_info=True)


@dataclass(slots=True)
class _TurnPrep:
    history: dspy.History
    should_route_rlm: bool
    routing_decision: str
    source_url: str | None
    core_memory: str
    selected_skills: list[str]
    active_skills: ActiveSkills


class EscalatingFleetModule(dspy.Module):
    """Unified DSPy Module that scales from lightweight chat to full RLM execution.

    Each turn is routed by a typed ``dspy.Predict(RouteTurnSignature)`` step:
    ``direct`` turns are answered by ``dspy.ChainOfThought``, ``tools`` turns
    run the shared ``dspy.ReAct`` loop
    (:class:`~fleet_rlm.runtime.agent.agent.FleetAgent`), and ``rlm`` turns run
    sandboxed Python via ``dspy.RLM``.  Explicit RLM modes, ``force_escalate``,
    URL-document analysis, and oversized contexts bypass the router and go
    deterministically to the RLM path.

    Parameters
    ----------
    interpreter:
        Daytona interpreter instance (may be ``None`` for unit tests).
    tools:
        Tool list shared by the ReAct loop and the RLM heavy path.
    max_iterations:
        Maximum RLM iterations for the heavy path.
    max_llm_calls:
        Maximum LLM calls for the heavy path.
    max_output_chars:
        Maximum REPL output characters exposed back to the RLM per step.
    action_max_tokens:
        Maximum model tokens for each RLM action-generation call.
    action_timeout:
        Per-call timeout (seconds) for RLM action-generation LM calls.
        After 2 consecutive timeouts the RLM falls back to extraction.
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
        action_max_tokens: int | None = None,
        action_timeout: int | None = None,
        verbose: bool = False,
        sub_lm: dspy.LM | None = None,
        summary_interval: int = 10,
    ) -> None:
        super().__init__()
        self._interpreter = interpreter
        self._summary_interval = summary_interval
        self._turn_count = 0
        self._turn_inputs_emitted = False
        self._tools = list(tools or [])
        self._available_tools_text = _describe_tools(self._tools)
        self._fallback_timeout = _FALLBACK_TIMEOUT

        from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule
        from fleet_rlm.runtime.tools._volume_paths import volume_root

        volume_mount_path = getattr(interpreter, "volume_mount_path", None) if interpreter is not None else None
        if volume_mount_path is None:
            resolved = volume_root()
            volume_mount_path = str(resolved) if resolved is not None else None
        self._skill_selector = SkillSelectionModule(volume_mount_path=volume_mount_path, lm=sub_lm)

        self.route = dspy.Predict(RouteTurnSignature)
        self.respond = dspy.ChainOfThought(RLMReActChatSignature)
        self.summarize = dspy.ChainOfThought(ConversationSummarySignature)

        # Tools branch: the shared upstream dspy.ReAct loop (FleetAgent).
        # The unified streaming path attaches a StreamListener to
        # ``_react.extract.predict`` so its final response streams tokens,
        # and replays the branch trajectory after the turn completes.
        from fleet_rlm.runtime.agent.agent import FleetAgent

        self._react = FleetAgent(
            tools=self._tools,
            max_iters=max(1, min(max_iterations, _REACT_MAX_ITERS)),
        )

        self._rlm: dspy.Module | None = None
        self._workspace_rlm: dspy.Module | None = None
        self._url_document_rlm: dspy.Module | None = None
        if interpreter is not None:
            rlm_tools = [*interpreter_delegation_tools(interpreter), *self._tools]
            rlm_output_chars = max_output_chars or VARIABLE_MODE_MAX_OUTPUT_CHARS
            self._rlm = create_runtime_rlm(
                signature=RLMTurnSignature,
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                max_output_chars=rlm_output_chars,
                action_max_tokens=action_max_tokens,
                action_timeout=action_timeout,
                verbose=verbose,
                tools=rlm_tools or None,
                sub_lm=sub_lm,
            )
            self._workspace_rlm = create_runtime_rlm(
                signature=RLMWorkspaceTurnSignature,
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                max_output_chars=rlm_output_chars,
                action_max_tokens=action_max_tokens,
                action_timeout=action_timeout,
                verbose=verbose,
                tools=rlm_tools or None,
                sub_lm=sub_lm,
            )
            self._url_document_rlm = create_runtime_rlm(
                signature=RLMDocumentTurnSignature,
                interpreter=interpreter,
                max_iterations=max(1, min(max_iterations, _URL_DOCUMENT_MAX_ITERATIONS)),
                max_llm_calls=max(1, min(max_llm_calls, _URL_DOCUMENT_MAX_LLM_CALLS)),
                max_output_chars=rlm_output_chars,
                action_max_tokens=action_max_tokens,
                action_timeout=action_timeout,
                verbose=verbose,
                sub_lm=sub_lm,
                include_llm_tools=not url_repl_only_enabled(),
            )

    def preview_routing(
        self,
        *,
        user_request: str,
        execution_mode: str = "auto",
        turn_context: TurnContext | None = None,
    ) -> dict[str, Any]:
        """Return deterministic routing metadata available before the full turn."""
        should_route, routing_decision, source_url = resolve_rlm_routing(
            execution_mode=execution_mode,
            user_request=user_request,
            force_escalate=False,
            turn_context=turn_context,
        )
        if should_route:
            payload: dict[str, Any] = {"routing_decision": routing_decision}
            if source_url:
                payload["source_url"] = source_url
            if routing_decision == "large_context_rlm" and turn_context is not None:
                payload["estimated_chars"] = getattr(turn_context, "estimated_chars", 0)
                payload["threshold_chars"] = getattr(turn_context, "threshold_chars", 0)
                payload["context_sources"] = list(getattr(turn_context, "context_sources", []) or [])
            return payload
        return {}

    def compress_history(self, history: dspy.History) -> str:
        """Return a compressed text summary of the given history."""
        messages = list(getattr(history, "messages", []) or [])
        if not messages:
            return ""
        history_text = "\n".join(
            turn_text for turn_text in (_history_turn_text(message) for message in messages) if turn_text
        )
        if not history_text:
            return ""
        try:
            result = self.summarize(conversation_history=history_text)
            return str(getattr(result, "summary", "") or "")
        except Exception as exc:
            logger.warning("Conversation summary failed, returning truncated history: %s", exc)
            return history_text[-4000:]

    def _resolve_skill_volume_mount_path(self) -> str | None:
        from fleet_rlm.runtime.tools._volume_paths import volume_root

        if self._interpreter is not None:
            mounted = getattr(self._interpreter, "volume_mount_path", None)
            if mounted:
                return str(mounted)
        resolved = volume_root()
        return str(resolved) if resolved is not None else None

    def _enrich_with_skills(
        self,
        user_request: str,
        core_memory: str,
        *,
        execution_mode: str = "auto",
        routing_decision: str | None = None,
        is_first_turn: bool = False,
    ) -> tuple[str, list[str], ActiveSkills]:
        """Select relevant skills and expose full instructions as RLM variables."""
        volume_mount_path = self._resolve_skill_volume_mount_path()
        if volume_mount_path != self._skill_selector._volume_mount_path:
            self._skill_selector._volume_mount_path = volume_mount_path
        try:
            selection = self._skill_selector(
                user_request=user_request,
                core_memory=core_memory,
                execution_mode=execution_mode,
                routing_decision=routing_decision,
                is_first_turn=is_first_turn,
            )
            skill_context = str(getattr(selection, "skill_context", "") or "")
            selected = [str(item) for item in list(getattr(selection, "selected_skills", []) or [])]
            active_skills = getattr(selection, "active_skills", None)
            if not isinstance(active_skills, ActiveSkills):
                active_skills = ActiveSkills(selected=selected)
            if skill_context:
                logger.debug("SkillSelection: selected %s", selected)
                enriched = f"{core_memory}\n\n{skill_context}" if core_memory else skill_context
                return enriched, selected, active_skills
            return core_memory, selected, active_skills
        except Exception as exc:
            logger.debug("SkillSelection: skipped (%s)", exc)
        return core_memory, [], ActiveSkills()

    def _prepare_turn(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History | None,
        execution_mode: str,
        force_escalate: bool,
        turn_context: TurnContext | None,
    ) -> _TurnPrep:
        if history is None:
            history = dspy.History(messages=[])

        self._turn_count += 1
        should_route_rlm, routing_decision, source_url = resolve_rlm_routing(
            execution_mode=execution_mode,
            user_request=user_request,
            force_escalate=force_escalate,
            turn_context=turn_context,
        )
        core_memory, selected_skills, active_skills = self._enrich_with_skills(
            user_request,
            core_memory,
            execution_mode=execution_mode,
            routing_decision=routing_decision if should_route_rlm else None,
            is_first_turn=self._turn_count == 1,
        )
        return _TurnPrep(
            history=history,
            should_route_rlm=should_route_rlm,
            routing_decision=routing_decision,
            source_url=source_url,
            core_memory=core_memory,
            selected_skills=selected_skills,
            active_skills=active_skills,
        )

    def _route_turn(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
    ) -> str:
        """Classify the turn via the typed router; degrade to ``direct`` on failure."""
        try:
            decision = self.route(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
                available_tools=self._available_tools_text or "(no tools available)",
            )
            route = str(getattr(decision, "route", "") or "").strip().lower()
        except Exception as exc:
            logger.warning("Turn routing failed (%s), defaulting to direct response", exc)
            return "direct"
        if route == "tools" and not self._tools:
            return "direct"
        if route == "rlm" and self._rlm is None:
            return "tools" if self._tools else "direct"
        if route in {"direct", "tools", "rlm"}:
            return route
        return "direct"

    def forward(
        self,
        *,
        user_request: str,
        core_memory: str = "",
        history: dspy.History | None = None,
        execution_mode: str = "auto",
        force_escalate: bool = False,
        conversation_summary: str = "",
        turn_context: TurnContext | None = None,
    ) -> dspy.Prediction:
        """Run one turn through the fleet program.

        Parameters
        ----------
        user_request:
            The current user message.
        core_memory:
            Serialized core memory string (persona, human, scratchpad blocks).
        history:
            Prior conversation turns as a :class:`dspy.History`.
        execution_mode:
            ``"auto"`` to let the typed router decide; ``"rlm"`` to force the
            heavy path; any other value uses the lightweight path.
        force_escalate:
            Bypass routing and go directly to RLM.
        conversation_summary:
            Pre-computed session summary; used on the RLM path to avoid an
            extra compression call.
        """
        self._turn_inputs_emitted = False

        prep = self._prepare_turn(
            user_request=user_request,
            core_memory=core_memory,
            history=history,
            execution_mode=execution_mode,
            force_escalate=force_escalate,
            turn_context=turn_context,
        )

        if prep.should_route_rlm:
            logger.debug(
                "EscalatingFleetModule: deterministic RLM path (mode=%s route=%s)",
                execution_mode,
                prep.routing_decision,
            )
            return self._run_rlm(
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                conversation_summary=conversation_summary,
                selected_skills=prep.selected_skills,
                active_skills=prep.active_skills,
                routing_decision=prep.routing_decision,
                source_url=prep.source_url,
                turn_context=turn_context,
            )

        route = self._route_turn(
            user_request=user_request,
            core_memory=prep.core_memory,
            history=prep.history,
        )

        if route == "tools":
            logger.debug("EscalatingFleetModule: router chose ReAct tool loop")
            return self._run_react(
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                selected_skills=prep.selected_skills,
            )
        if route == "rlm":
            logger.debug("EscalatingFleetModule: router chose RLM path")
            return self._run_rlm(
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                conversation_summary=conversation_summary,
                selected_skills=prep.selected_skills,
                active_skills=prep.active_skills,
                routing_decision="router_rlm",
                source_url=None,
                turn_context=turn_context,
            )

        # CoT path: emit 3 turn-input rows before responding
        cot_rows = [
            TurnInputRow(
                label="Request",
                kind="request",
                value=user_request,
                preview=_preview_text(user_request),
            ),
            TurnInputRow(
                label="History",
                kind="history",
                value=list(getattr(prep.history, "messages", []) or []),
                preview=_history_preview(prep.history),
            ),
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=prep.core_memory,
                preview=_preview_text(prep.core_memory),
            ),
        ]
        _emit_turn_inputs(self._interpreter, cot_rows, module=self)

        prediction = self.respond(
            user_request=user_request,
            core_memory=prep.core_memory,
            history=prep.history,
        )
        _prediction_set(prediction, "selected_skills", prep.selected_skills)
        return prediction

    async def aforward(
        self,
        *,
        user_request: str,
        core_memory: str = "",
        history: dspy.History | None = None,
        execution_mode: str = "auto",
        force_escalate: bool = False,
        conversation_summary: str = "",
        turn_context: TurnContext | None = None,
    ) -> dspy.Prediction:
        """Run one turn without blocking async callers.

        Heavy RLM work stays on the synchronous path inside a worker thread
        (with token streaming disabled) because sandbox execution is blocking.
        The tools branch uses ``acall`` so session-backed async tools,
        including MCP tools, are awaited correctly, and the direct branch uses
        ``respond.acall`` so its ``response`` field can stream tokens natively
        under ``dspy.streamify``.
        """
        self._turn_inputs_emitted = False

        prep = await _to_thread_unstreamed(
            self._prepare_turn,
            user_request=user_request,
            core_memory=core_memory,
            history=history,
            execution_mode=execution_mode,
            force_escalate=force_escalate,
            turn_context=turn_context,
        )

        if prep.should_route_rlm:
            return await _to_thread_unstreamed(
                self._run_rlm,
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                conversation_summary=conversation_summary,
                selected_skills=prep.selected_skills,
                active_skills=prep.active_skills,
                routing_decision=prep.routing_decision,
                source_url=prep.source_url,
                turn_context=turn_context,
            )

        route = await _to_thread_unstreamed(
            self._route_turn,
            user_request=user_request,
            core_memory=prep.core_memory,
            history=prep.history,
        )

        if route == "tools":
            logger.debug("EscalatingFleetModule: router chose ReAct tool loop (async)")
            return await self._arun_react(
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                selected_skills=prep.selected_skills,
            )
        if route == "rlm":
            logger.debug("EscalatingFleetModule: router chose RLM path (async)")
            return await _to_thread_unstreamed(
                self._run_rlm,
                user_request=user_request,
                core_memory=prep.core_memory,
                history=prep.history,
                conversation_summary=conversation_summary,
                selected_skills=prep.selected_skills,
                active_skills=prep.active_skills,
                routing_decision="router_rlm",
                source_url=None,
                turn_context=turn_context,
            )

        # CoT path (async): emit 3 turn-input rows before responding
        cot_rows = [
            TurnInputRow(
                label="Request",
                kind="request",
                value=user_request,
                preview=_preview_text(user_request),
            ),
            TurnInputRow(
                label="History",
                kind="history",
                value=list(getattr(prep.history, "messages", []) or []),
                preview=_history_preview(prep.history),
            ),
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=prep.core_memory,
                preview=_preview_text(prep.core_memory),
            ),
        ]
        _emit_turn_inputs(self._interpreter, cot_rows, module=self)

        prediction = await self.respond.acall(
            user_request=user_request,
            core_memory=prep.core_memory,
            history=prep.history,
        )
        _prediction_set(prediction, "selected_skills", prep.selected_skills)
        return prediction

    def _react_fallback(
        self,
        prediction: dspy.Prediction,
        *,
        selected_skills: list[str] | None,
    ) -> dspy.Prediction:
        prediction["degraded"] = True
        prediction["warning"] = _REACT_FALLBACK_WARNING
        prediction["runtime_degraded"] = True
        prediction["runtime_failure_category"] = "react_fallback"
        prediction["runtime_failure_phase"] = "escalating_react"
        prediction["runtime_fallback_used"] = True
        prediction["runtime_warning"] = _REACT_FALLBACK_WARNING
        prediction["selected_skills"] = selected_skills or []
        prediction["routing_decision"] = "tools_react"
        return prediction

    def _run_react(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
        selected_skills: list[str] | None = None,
    ) -> dspy.Prediction:
        """Run the shared dspy.ReAct tool loop for the tools branch.

        The ReAct prediction carries its native ``trajectory`` (thought/tool/
        observation per step) so the streaming layer surfaces tool calls and
        results without extra adaptation. On failure the module degrades to the
        lightweight ChainOfThought response, mirroring the RLM fallback contract.
        """
        # Emit 3 turn-input rows before ReAct execution
        react_rows = [
            TurnInputRow(
                label="Request",
                kind="request",
                value=user_request,
                preview=_preview_text(user_request),
            ),
            TurnInputRow(
                label="History",
                kind="history",
                value=list(getattr(history, "messages", []) or []),
                preview=_history_preview(history),
            ),
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=core_memory,
                preview=_preview_text(core_memory),
            ),
        ]
        _emit_turn_inputs(self._interpreter, react_rows, module=self)

        try:
            result = self._react(chat_history=history, user_message=user_request)
            _prediction_set(result, "selected_skills", selected_skills or [])
            _prediction_set(result, "routing_decision", "tools_react")
            return result
        except Exception as exc:
            logger.warning(
                "EscalatingFleetModule: ReAct tool loop failed (%s), falling back to ChainOfThought",
                exc,
            )
            fallback = self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
            )
            return self._react_fallback(fallback, selected_skills=selected_skills)

    async def _arun_react(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
        selected_skills: list[str] | None = None,
    ) -> dspy.Prediction:
        """Async ReAct branch for session-backed tools such as MCP."""
        # Emit 3 turn-input rows before async ReAct execution
        react_rows = [
            TurnInputRow(
                label="Request",
                kind="request",
                value=user_request,
                preview=_preview_text(user_request),
            ),
            TurnInputRow(
                label="History",
                kind="history",
                value=list(getattr(history, "messages", []) or []),
                preview=_history_preview(history),
            ),
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=core_memory,
                preview=_preview_text(core_memory),
            ),
        ]
        _emit_turn_inputs(self._interpreter, react_rows, module=self)

        try:
            result = await self._react.acall(chat_history=history, user_message=user_request)
            _prediction_set(result, "selected_skills", selected_skills or [])
            _prediction_set(result, "routing_decision", "tools_react")
            return result
        except Exception as exc:
            logger.warning(
                "EscalatingFleetModule: async ReAct tool loop failed (%s), falling back to ChainOfThought",
                exc,
            )
            fallback = await self.respond.acall(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
            )
            return self._react_fallback(fallback, selected_skills=selected_skills)

    def _run_rlm(
        self,
        *,
        user_request: str,
        core_memory: str,
        history: dspy.History,
        conversation_summary: str,
        selected_skills: list[str] | None = None,
        active_skills: ActiveSkills | None = None,
        routing_decision: str = "rlm",
        source_url: str | None = None,
        turn_context: TurnContext | None = None,
    ) -> dspy.Prediction:
        if self._rlm is None:
            # CoT fallback from RLM path: emit 3 turn-input rows
            cot_fallback_rows = [
                TurnInputRow(
                    label="Request",
                    kind="request",
                    value=user_request,
                    preview=_preview_text(user_request),
                ),
                TurnInputRow(
                    label="History",
                    kind="history",
                    value=list(getattr(history, "messages", []) or []),
                    preview=_history_preview(history),
                ),
                TurnInputRow(
                    label="Core memory",
                    kind="core_memory",
                    value=core_memory,
                    preview=_preview_text(core_memory),
                ),
            ]
            _emit_turn_inputs(self._interpreter, cot_fallback_rows, module=self)
            prediction = self.respond(
                user_request=user_request,
                core_memory=core_memory,
                history=history,
            )
            _prediction_set(prediction, "selected_skills", selected_skills or [])
            return prediction
        _emit_turn_milestone(
            self._interpreter,
            phase="rlm_start",
            text="Running RLM analysis...",
            routing_decision=routing_decision,
        )
        if turn_context is not None:
            estimated = getattr(turn_context, "estimated_chars", 0)
            _emit_turn_milestone(
                self._interpreter,
                phase="context_estimate",
                text=f"Large context detected ({estimated} chars) — using dspy.RLM",
                estimated_chars=estimated,
                routing_decision=routing_decision,
            )
        context = conversation_summary or self.compress_history(history)
        url_document_mode = bool(source_url and self._url_document_rlm is not None)
        large_context_mode = (
            routing_decision == "large_context_rlm" and turn_context is not None and self._workspace_rlm is not None
        )
        core_context = build_rlm_core_context(
            user_request=user_request,
            compressed_history=context,
            core_memory=core_memory,
            url_document_mode=url_document_mode,
            large_context_mode=large_context_mode,
        )
        call_kwargs: dict[str, Any] = {
            "user_request": user_request,
            "core_memory": core_context,
            "history": history,
            "active_skills": active_skills or ActiveSkills(selected=selected_skills or []),
        }
        rlm = self._rlm
        if url_document_mode:
            from fleet_rlm.integrations.observability.mlflow_context import (
                mlflow_child_span,
                set_mlflow_span_outputs,
            )

            _emit_turn_milestone(
                self._interpreter,
                phase="document_fetch",
                text=f"Fetching document from {source_url}...",
                source_url=source_url,
            )
            with mlflow_child_span(
                "fleet_rlm.fetch_url_document",
                span_type="TOOL",
                attributes={
                    "fleet_rlm.source_url": str(source_url or ""),
                    "fleet_rlm.routing_decision": routing_decision,
                },
                inputs={"source_url": source_url},
            ) as span:
                fetched = fetch_url_document(interpreter=self._interpreter, source_url=source_url)
                set_mlflow_span_outputs(
                    span,
                    {
                        "source_url": fetched.source_url,
                        "document_chars": len(fetched.document_text),
                        "metadata_keys": sorted(str(key) for key in fetched.source_metadata),
                    },
                )
            call_kwargs["document"] = LargeDocument(
                text=fetched.document_text,
                source_url=fetched.source_url,
                metadata=fetched.source_metadata,
            )
            rlm = self._url_document_rlm
        elif large_context_mode:
            from fleet_rlm.runtime.modules.context_routing import load_large_context_rlm_kwargs

            large_kwargs = load_large_context_rlm_kwargs(turn_context, interpreter=self._interpreter)
            estimated = getattr(turn_context, "estimated_chars", 0)
            _emit_turn_milestone(
                self._interpreter,
                phase="large_context_prepare",
                text=f"Large context in REPL variables ({estimated} chars)...",
                estimated_chars=estimated,
            )
            call_kwargs["context"] = WorkspaceContext(
                document_text=str(large_kwargs.get("document_text") or ""),
                context_paths=list(large_kwargs.get("context_paths") or []),
                manifest=dict(large_kwargs.get("context_manifest") or {}),
                metadata=dict(large_kwargs.get("source_metadata") or {}),
            )
            rlm = self._workspace_rlm

        # Emit 5 turn-input rows for RLM path before execution
        context_value = call_kwargs.get("context") or call_kwargs.get("document") or context
        rlm_rows = [
            TurnInputRow(
                label="Request",
                kind="request",
                value=user_request,
                preview=_preview_text(user_request),
            ),
            TurnInputRow(
                label="Active skills",
                kind="skills",
                value=call_kwargs.get("active_skills"),
                preview=_preview_text(str(call_kwargs.get("active_skills"))),
            ),
            TurnInputRow(
                label="History",
                kind="history",
                value=list(getattr(history, "messages", []) or []),
                preview=_history_preview(history),
            ),
            TurnInputRow(
                label="Core memory",
                kind="core_memory",
                value=call_kwargs.get("core_memory"),
                preview=_preview_text(str(call_kwargs.get("core_memory"))),
            ),
            TurnInputRow(
                label="Context",
                kind="context",
                value=context_value,
                preview=_preview_text(str(context_value)),
            ),
        ]
        _emit_turn_inputs(self._interpreter, rlm_rows, module=self)

        # Reset the chosen RLM instance's serializable-var cache once per
        # ``_run_rlm`` invocation so the primary call and any corrective /
        # parse-error / timeout retry share the freshly serialized variables
        # (the 4.3s ``rlm_prepare_variables`` cost is paid once per turn, not
        # once per retry). The ``(name, id(val))`` cache key in
        # ``_prepare_serializable_vars`` guarantees content-safety across turns
        # even if this reset is somehow bypassed.
        prepared_cache = getattr(rlm, "_prepared_serializable_cache", None)
        if isinstance(prepared_cache, dict):
            prepared_cache.clear()

        try:
            from fleet_rlm.integrations.observability.mlflow_context import (
                mlflow_child_span,
                set_mlflow_span_outputs,
            )

            with mlflow_child_span(
                "fleet_rlm.rlm_run",
                span_type="CHAIN",
                attributes={
                    "fleet_rlm.routing_decision": routing_decision,
                    "fleet_rlm.rlm_url_document_mode": str(url_document_mode).lower(),
                    "fleet_rlm.rlm_large_context_mode": str(large_context_mode).lower(),
                    "fleet_rlm.selected_skills": ",".join(selected_skills or []),
                    "fleet_rlm.active_skills_variable": str(active_skills is not None).lower(),
                    "fleet_rlm.rlm_action_max_tokens": str(getattr(rlm, "action_max_tokens", "") or ""),
                    "fleet_rlm.rlm_max_output_chars": str(getattr(rlm, "max_output_chars", "") or ""),
                },
                inputs={
                    "input_fields": sorted(call_kwargs),
                    "source_url": source_url,
                },
            ) as span:
                # Emit rlm_start event for real-time progress in the frontend
                _emit_turn_milestone(
                    self._interpreter,
                    phase="rlm_start",
                    text=(
                        f"Starting RLM analysis (routing: {routing_decision}, "
                        f"skills: {', '.join(selected_skills or [])})"
                    ),
                    source_type="rlm_progress",
                    routing_decision=routing_decision,
                    selected_skills=selected_skills,
                    max_iterations=getattr(rlm, "max_iterations", None),
                )
                result = rlm(**call_kwargs)

                # Check for malformed results before accepting
                if _is_malformed_rlm_result(result):
                    logger.warning(
                        "EscalatingFleetModule: RLM returned malformed result, retrying with corrective instruction"
                    )
                    # Retry once with corrective instruction in core_memory
                    corrective_memory = f"{core_memory}\n\n[IMPORTANT: Please provide your response as valid JSON with 'reasoning' and 'answer' fields.]"
                    retry_kwargs = {**call_kwargs, "core_memory": corrective_memory}
                    result = _run_with_timeout(
                        lambda: rlm(**retry_kwargs),
                        timeout=self._fallback_timeout,
                    )

                has_trajectory = getattr(result, "trajectory", None) is not None
                set_mlflow_span_outputs(
                    span,
                    {
                        "routing_decision": routing_decision,
                        "has_trajectory": has_trajectory,
                    },
                )
                if not has_trajectory:
                    logger.error(
                        "RLM produced no trajectory after %s iterations — "
                        "action generation likely failed. Raising error to surface to user.",
                        getattr(result, "iterations", "unknown"),
                    )
                    raise RuntimeError(
                        "RLM execution failed: no trajectory was produced. "
                        "The model was unable to generate executable actions for your request. "
                        "Try rephrasing or breaking the task into smaller steps."
                    )
                # Emit rlm_complete event for real-time progress in the frontend
                _emit_turn_milestone(
                    self._interpreter,
                    phase="rlm_complete",
                    text=f"RLM analysis complete (trajectory: {has_trajectory})",
                    source_type="rlm_progress",
                    has_trajectory=has_trajectory,
                    iterations=getattr(result, "iterations", 0),
                )
            _prediction_set(result, "selected_skills", selected_skills or [])
            _prediction_set(result, "routing_decision", routing_decision)
            if source_url:
                _prediction_set(result, "source_url", source_url)
            return result
        except Exception as exc:
            # Check if this is a parse error that we can retry
            if _is_rlm_parse_error(exc):
                logger.warning(
                    "EscalatingFleetModule: RLM parse error (%s), retrying with corrective instruction",
                    exc,
                )
                try:
                    # Retry once with corrective instruction
                    corrective_memory = f"{core_memory}\n\n[IMPORTANT: Please provide your response as valid JSON with 'reasoning' and 'answer' fields.]"
                    retry_kwargs = {**call_kwargs, "core_memory": corrective_memory}
                    result = _run_with_timeout(
                        lambda: rlm(**retry_kwargs),
                        timeout=self._fallback_timeout,
                    )
                    _prediction_set(result, "selected_skills", selected_skills or [])
                    _prediction_set(result, "routing_decision", routing_decision)
                    if source_url:
                        _prediction_set(result, "source_url", source_url)
                    return result
                except Exception as retry_exc:
                    logger.warning(
                        "EscalatingFleetModule: RLM retry also failed (%s), falling back to ChainOfThought",
                        retry_exc,
                    )
            else:
                # Non-parse exception (e.g. repeated action-gen timeouts raised
                # as dspy.LMError). Warn + retry once with a reduced budget
                # (lower max_iterations / max_output_chars) before falling back
                # to the lightweight responder. Never silent: a successful retry
                # still surfaces a soft warning so the user knows it retried.
                logger.warning(
                    "EscalatingFleetModule: RLM path failed (%s), retrying with reduced budget",
                    exc,
                )
                try:
                    saved_max_iter = getattr(rlm, "max_iterations", None)
                    saved_max_chars = getattr(rlm, "max_output_chars", None)
                    lowered_iter = isinstance(saved_max_iter, int)
                    lowered_chars = isinstance(saved_max_chars, int)
                    try:
                        if lowered_iter:
                            setattr(rlm, "max_iterations", max(1, min(int(saved_max_iter), 6)))
                        if lowered_chars:
                            setattr(rlm, "max_output_chars", min(int(saved_max_chars), 5000))
                        result = _run_with_timeout(
                            lambda: rlm(**call_kwargs),
                            timeout=self._fallback_timeout,
                        )
                    finally:
                        if lowered_iter:
                            setattr(rlm, "max_iterations", saved_max_iter)
                        if lowered_chars:
                            setattr(rlm, "max_output_chars", saved_max_chars)
                    _prediction_set(result, "selected_skills", selected_skills or [])
                    _prediction_set(result, "routing_decision", routing_decision)
                    _prediction_set(result, "runtime_degraded", True)
                    _prediction_set(
                        result,
                        "runtime_warning",
                        "RLM retried with a reduced budget after an action-generation failure; output may be incomplete.",
                    )
                    _prediction_set(result, "runtime_failure_category", "rlm_reduced_retry")
                    _prediction_set(result, "runtime_failure_phase", "escalating_rlm_retry")
                    _prediction_set(result, "runtime_fallback_used", True)
                    if source_url:
                        _prediction_set(result, "source_url", source_url)
                    return result
                except Exception as retry_exc:
                    logger.warning(
                        "EscalatingFleetModule: RLM reduced-budget retry also failed (%s), falling back to ChainOfThought",
                        retry_exc,
                    )

            logger.warning("EscalatingFleetModule: RLM path failed (%s), falling back to ChainOfThought", exc)
            # Cap the fallback responder with stateless config overrides (per-IO
            # timeout, qwen thinking off) so it can't run unbounded on the planner.
            from fleet_rlm.runtime.config import build_lm_config

            base_lm = getattr(dspy.settings, "lm", None)
            config_overrides = build_lm_config(
                base_lm,
                max_tokens=2048,
                temperature=0.0,
                timeout=float(self._fallback_timeout),
                num_retries=0,
            )

            def _respond() -> Any:
                kw = {"config": config_overrides} if config_overrides else {}
                return self.respond(
                    user_request=user_request,
                    core_memory=core_memory,
                    history=history,
                    **kw,
                )

            try:
                fallback = _run_with_timeout(_respond, timeout=self._fallback_timeout)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "EscalatingFleetModule: fallback respond timed out after %ss",
                    self._fallback_timeout,
                )
                # Terminal fallback timed out — synthesize a degraded payload
                # rather than letting litellm/tenacity retries hold the turn.
                fallback = dspy.Prediction(
                    reasoning="",
                    response=(
                        "I apologize, but I was unable to process your request within the "
                        "available time. The RLM analysis pipeline could not complete, and "
                        "the fallback response also timed out.\n\n"
                        "**Suggestions:**\n"
                        "- Try breaking your request into smaller, more specific questions\n"
                        "- Reduce the amount of context or files referenced\n"
                        "- Try again — temporary provider latency may have caused this\n\n"
                        f"(Error: RLM escalation and fallback both timed out after "
                        f"{self._fallback_timeout}s)"
                    ),
                )
                fallback["degraded"] = True
                fallback["warning"] = "RLM escalation and fallback both timed out."
                fallback["runtime_degraded"] = True
                fallback["runtime_failure_category"] = "rlm_fallback_timeout"
                fallback["runtime_failure_phase"] = "escalating_rlm_timeout"
                fallback["runtime_fallback_used"] = True
                fallback["runtime_warning"] = "RLM escalation and fallback both timed out."
                fallback["selected_skills"] = selected_skills or []
                fallback["routing_decision"] = routing_decision
                if source_url:
                    fallback["source_url"] = source_url
                return fallback
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


__all__ = [
    "EscalatingFleetModule",
]
