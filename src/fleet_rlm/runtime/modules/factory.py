"""Factory functions and shared config for constructing DSPy runtime modules.

Performance-improvement flags (all env-configurable, default safe):

- ``FLEET_RLM_SUMMARY_ITERATION_THRESHOLD`` (default 20) — if the RLM loop
  reaches this iteration index without receiving a ``SUBMIT``, the base RLM
  wrapper injects a "summarize and SUBMIT now" directive into ``repl_history``
  so the agent stops exploring and produces a final answer instead of burning
  the full ``max_iterations`` budget.

- ``FLEET_RLM_CONTEXT_PREPARSE_ENABLED`` (default "1") — when a
  ``WorkspaceContext`` is staged with a ``document_text`` exceeding a
  threshold, a structured index (sections, file paths, heading offsets) is
  pre-computed and injected as the ``context_index`` REPL variable so the agent
  does not spend 15+ iterations discovering the document layout via trial-and-
  error regex. The index replaces the need for iterative ``document.find()``
  calls, eliminating redundant REPL round-trips and LLM action generations.

- ``FLEET_RLM_REPL_OUTPUT_CACHE_ENABLED`` (default "1") — caches REPL outputs
  by code-hash across iterations within a single ``forward()`` call so that
  identical subsequent code blocks return the cached result without sandbox
  round-trip or model action regeneration.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable

import dspy
from dspy.predict.rlm import _strip_code_fences

from fleet_rlm.runtime.content.parse_recovery import (
    extract_completion_from_parse_error as _extract_completion,
)
from fleet_rlm.runtime.content.parse_recovery import (
    is_degenerate_response as _is_degenerate,
)
from fleet_rlm.runtime.content.parse_recovery import (
    truncate_completion as _truncate,
)

logger = logging.getLogger(__name__)

_DSPY_RLM_BASE: Any = dspy.RLM


def _env_int(name: str, default: int) -> int:
    """Read an integer from environment variable, returning default if not set or invalid."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# Threshold (chars) above which a turn auto-routes to the RLM sandbox.
VARIABLE_MODE_THRESHOLD = 32_000

# Lower max_output_chars for sandbox-heavy paths forces the LLM to work
# through REPL variables (peek, grep, sub_rlm) instead of printing large
# output back into its own context.
VARIABLE_MODE_MAX_OUTPUT_CHARS = _env_int("FLEET_RLM_VARIABLE_MODE_MAX_OUTPUT_CHARS", 10_000)
RLM_ACTION_HISTORY_RECENT_ENTRIES = 4
RLM_ACTION_HISTORY_MAX_ENTRIES = 8
RLM_ACTION_HISTORY_OUTPUT_CHARS = 1_500
RLM_ACTION_HISTORY_CODE_CHARS = 1_200
RLM_ACTION_HISTORY_REASONING_CHARS = 600

# ── P0: Compressed tool docs ─────────────────────────────────────────────────
# Instead of injecting the full tool descriptions (30+ tools, thousands of chars)
# into every action-generation prompt, compact them into one-liners with an
# "use inspect_tool()" escape hatch.  Reduces prompt size by 60-80%.
_COMPRESSED_TOOL_DOCS_ENABLED = os.environ.get("FLEET_RLM_COMPRESSED_TOOL_DOCS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# ── P1: Iteration budget guardrail ─────────────────────────────────────────
# If the RLM loop reaches this iteration index without receiving a ``SUBMIT``,
# inject a "summarize and SUBMIT now" directive into ``repl_history`` so the
# agent produces a final answer instead of burning the full budget.
_SUMMARY_ITERATION_THRESHOLD = _env_int("FLEET_RLM_SUMMARY_ITERATION_THRESHOLD", 20)

# ── P1: Context pre-processing ─────────────────────────────────────────────
# When a WorkspaceContext ``document_text`` exceeds this many characters, a
# structured index is pre-computed and injected as the ``context_index`` REPL
# variable so the agent does not spend iterations discovering document layout.
_CONTEXT_PREPARSE_ENABLED = os.environ.get("FLEET_RLM_CONTEXT_PREPARSE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_CONTEXT_PREPARSE_THRESHOLD = _env_int("FLEET_RLM_CONTEXT_PREPARSE_THRESHOLD", 32_000)

# ── P2: REPL output cache ─────────────────────────────────────────────────
# Caches REPL outputs by code-hash across iterations within a single
# ``forward()`` call so that identical code blocks return the cached result
# without a sandbox round-trip or model action regeneration.
_REPL_OUTPUT_CACHE_ENABLED = os.environ.get("FLEET_RLM_REPL_OUTPUT_CACHE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# Tools that keep their full docs even when compression is on
_TOOL_DOC_KEEP_FULL: frozenset[str] = frozenset(
    {
        "SUBMIT",
        "llm_query",
        "llm_query_batched",
    }
)

_COMPRESSED_TOOLS_INTRO = (
    "Available tools (import and inspect for full signatures):\n"
    '- `llm_query(prompt: string, context: string = "")` — query a sub-LLM for semantic analysis. Pass workspace content explicitly via `context` (e.g. `llm_query("summarise", context[\'document_text\'][:50_000])`); the sub-LLM does NOT auto-see sandbox `context`.\n'
    '- `llm_query_batched(prompts: list[string], context: string = "")` — query multiple prompts concurrently (context prepended to each)\n'
    "- `SUBMIT(response=...)` — submit final answer\n"
    "- `inspect_tool(name: string)` — print any tool's full signature and docs\n"
    "- Other REPL callables: see `dir()` for the full list; use `inspect_tool()` for details."
)


def _compact_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(text) - max_chars
    return f"{text[:head]}\n... ({omitted:,} chars omitted) ...\n{text[-tail:]}"


def _compact_repl_history_for_action(history: Any) -> Any:
    """Return a compact prompt-only REPL history for action generation."""
    entries = list(getattr(history, "entries", []) or [])
    if len(entries) <= RLM_ACTION_HISTORY_MAX_ENTRIES:
        return history

    from dspy.primitives.repl_types import REPLEntry, REPLHistory

    older = entries[:-RLM_ACTION_HISTORY_RECENT_ENTRIES]
    recent = entries[-RLM_ACTION_HISTORY_RECENT_ENTRIES:]
    summary_lines: list[str] = []
    for index, entry in enumerate(older, start=1):
        code = _compact_text(getattr(entry, "code", ""), max_chars=240).replace("\n", "\\n")
        output = _compact_text(getattr(entry, "output", ""), max_chars=320).replace("\n", "\\n")
        summary_lines.append(f"step {index}: code={code!r}; output={output!r}")

    compact_entries = [
        REPLEntry(
            reasoning=f"Compressed {len(older)} earlier REPL steps for action prompt budget.",
            code="# Earlier REPL steps were compressed for this action-generation prompt.",
            output="\n".join(summary_lines),
        )
    ]
    compact_entries.extend(
        REPLEntry(
            reasoning=_compact_text(getattr(entry, "reasoning", ""), max_chars=RLM_ACTION_HISTORY_REASONING_CHARS),
            code=_compact_text(getattr(entry, "code", ""), max_chars=RLM_ACTION_HISTORY_CODE_CHARS),
            output=_compact_text(getattr(entry, "output", ""), max_chars=RLM_ACTION_HISTORY_OUTPUT_CHARS),
        )
        for entry in recent
    )
    return REPLHistory(
        entries=compact_entries,
        max_output_chars=min(
            int(
                getattr(history, "max_output_chars", RLM_ACTION_HISTORY_OUTPUT_CHARS) or RLM_ACTION_HISTORY_OUTPUT_CHARS
            ),
            RLM_ACTION_HISTORY_OUTPUT_CHARS,
        ),
    )


def interpreter_delegation_tools(interpreter: Any | None) -> list[Any]:
    """Collect the interpreter's recursive delegation callables as plain tools."""
    tools: list[Any] = []
    if interpreter is None:
        return tools
    for attr_name in ("sub_rlm", "sub_rlm_batched"):
        fn = getattr(interpreter, attr_name, None)
        if callable(fn):
            tools.append(fn)
    return tools


class _EmittingAction(dspy.Module):
    """Wraps the RLM's ``generate_action`` predictor to stream each step.

    Emits ``rlm_reasoning`` and ``rlm_tool_call`` as soon as the action LM
    call returns, before sandbox execution starts, so chat clients see
    progress in real time.
    """

    def __init__(self, inner: Any, emit: Callable[[dict[str, Any]], None]) -> None:
        super().__init__()
        self._inner = inner
        self._emit = emit
        self.current_iteration = 0
        self.action_max_tokens: int | None = None

    def __getattr__(self, name: str) -> Any:
        # Delegate predictor attributes (signature, demos, ...) to the wrapped
        # Predict so optimizers and introspection keep working.
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    @staticmethod
    def _iteration_index(kwargs: dict[str, Any]) -> int:
        raw = str(kwargs.get("iteration", "") or "")
        head = raw.split("/", 1)[0].strip()
        try:
            return max(0, int(head) - 1)
        except ValueError:
            return 0

    def _emit_action(self, prediction: Any, iteration: int) -> None:
        reasoning = str(getattr(prediction, "reasoning", "") or "")
        code_raw = str(getattr(prediction, "code", "") or "")
        try:
            code = _strip_code_fences(code_raw)
        except SyntaxError:
            code = code_raw
        self._emit(
            {
                "phase": "rlm_reasoning",
                "iteration": iteration,
                "reasoning": reasoning,
                "code_preview": code[:500],
            }
        )
        self._emit(
            {
                "phase": "rlm_tool_call",
                "iteration": iteration,
                "code": code,
                "tool_name": "repl_execute",
            }
        )

    def forward(self, **kwargs: Any) -> Any:
        self.current_iteration = self._iteration_index(kwargs)
        if "repl_history" in kwargs:
            kwargs["repl_history"] = _compact_repl_history_for_action(kwargs["repl_history"])
        if self.action_max_tokens is not None:
            config = dict(kwargs.pop("config", {}) or {})
            config["max_tokens"] = self.action_max_tokens
            kwargs["config"] = config
        prediction = self._inner(**kwargs)
        self._emit_action(prediction, self.current_iteration)
        return prediction

    async def aforward(self, **kwargs: Any) -> Any:
        self.current_iteration = self._iteration_index(kwargs)
        if "repl_history" in kwargs:
            kwargs["repl_history"] = _compact_repl_history_for_action(kwargs["repl_history"])
        if self.action_max_tokens is not None:
            config = dict(kwargs.pop("config", {}) or {})
            config["max_tokens"] = self.action_max_tokens
            kwargs["config"] = config
        prediction = await self._inner.acall(**kwargs)
        self._emit_action(prediction, self.current_iteration)
        return prediction


class _StreamingRLM(_DSPY_RLM_BASE):
    """``dspy.RLM`` that streams per-iteration progress via interpreter callback.

    Instead of re-implementing ``_execute_iteration``, this subclass hooks two
    stable seams: the ``generate_action`` predictor (action streaming) and
    ``_process_execution_result`` (sandbox output streaming).
    """

    def __init__(
        self,
        *args: Any,
        action_max_tokens: int | None = None,
        action_timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.generate_action = _EmittingAction(self.generate_action, self._emit_step)
        self.generate_action.action_max_tokens = action_max_tokens
        self.action_max_tokens = action_max_tokens
        self.action_timeout = action_timeout if action_timeout is not None else _env_int("FLEET_RLM_ACTION_TIMEOUT", 90)
        self._consecutive_timeouts = 0
        self._max_consecutive_timeouts = 2
        # Consecutive action-gen parse failures the adapter's native fallback
        # (ChatAdapter → JSONAdapter, ``dspy/adapters/chat_adapter.py:46,68,87-94``)
        # could not recover. Bounded so a persistently glitching model escalates
        # to EscalatingFleetModule instead of burning max_iterations.
        self._consecutive_parse_errors = 0
        self._max_consecutive_parse_errors = _env_int("FLEET_RLM_MAX_CONSECUTIVE_PARSE_ERRORS", 3)
        # Per-instance cache (NOT class-level) so two RLM instances with the
        # same variable names but different content do not share cached data.
        # Cleared at the start of each forward() call to prevent stale data
        # from a previous turn leaking into the next turn.
        self._prepared_serializable_cache: dict[frozenset[str], dict[str, Any]] = {}
        # P2: REPL output cache — keyed by code hash, per forward() call
        self._repl_output_cache: dict[str, str] = {}
        # P1: Track whether the summary guardrail has been injected this turn
        self._summary_directive_injected = False

    def _emit_step(self, payload: dict[str, Any]) -> None:
        interpreter = getattr(self, "_interpreter", None)
        if interpreter is None:
            return
        callback = getattr(interpreter, "_turn_step_callback", None)
        if not callable(callback):
            return
        try:
            callback(payload)
        except Exception:
            return

    # ── P0: Compressed tool docs override ─────────────────────────────────
    def _format_tool_docs(self, tools: dict[str, Any]) -> str:
        """Override base RLM tool-doc formatting to compress non-essential docs.

        When ``FLEET_RLM_COMPRESSED_TOOL_DOCS`` is enabled (default), only
        ``SUBMIT``, ``llm_query``, and ``llm_query_batched`` get full inline
        descriptions.  All other tools are listed by name only; the LLM can
        ``inspect_tool(name)`` at runtime for full signatures — just like a
        human developer in a REPL.

        This reduces the action-generation system prompt by ~60-80% (from
        ~15,000 chars to ~3,000 chars), directly cutting the dominant latency
        contributor (the bounded action-LM inference on the massive system prompt).
        """
        if not tools:
            return ""

        if not _COMPRESSED_TOOL_DOCS_ENABLED:
            # Legacy: full inline docs for every tool.
            return super()._format_tool_docs(tools)

        lines = ["\n" + _COMPRESSED_TOOLS_INTRO]

        # Add one-liner names for all other tools
        other_names = sorted(name for name in tools if name not in _TOOL_DOC_KEEP_FULL)
        if other_names:
            lines.append("Other callables: " + ", ".join(f"`{n}`" for n in other_names))

        return "\n".join(lines)

    def _structured_action_context(self) -> Any:
        """RLM action generation runs under DSPy's default adapter (ChatAdapter).

        ChatAdapter automatically falls back to JSONAdapter on parse failure
        (``dspy/adapters/chat_adapter.py:46,68,87-94``); forcing JSONAdapter as
        the primary caused a marker mismatch with qwen3.x output (which emits
        ``[[ ## reasoning ## ]]`` / ``[[ ## code ## ]]``) and required a
        hand-rolled salvage cascade — both removed in this change. Returning a
        no-op context lets DSPy's default adapter resolution (configured via
        ``DSPY_STRUCTURED_OUTPUT_ADAPTER``) take effect.
        """
        return nullcontext()

    def _record_iteration_token_usage(self, iteration: int) -> None:
        """Extract per-iteration token usage from dspy LM history and record as MLflow span attr."""
        try:
            lm = getattr(dspy.settings, "lm", None)
            if lm is None:
                return
            history = getattr(lm, "history", None)
            if not history or not isinstance(history, list):
                return
            last_entry = history[-1] if history else None
            if not isinstance(last_entry, dict):
                return
            usage = last_entry.get("usage") or last_entry.get("token_usage")
            if not isinstance(usage, dict):
                return
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            if input_tokens is None and output_tokens is None:
                return
            self._emit_step(
                {
                    "phase": "rlm_iteration_tokens",
                    "iteration": iteration,
                    "input_tokens": int(input_tokens) if input_tokens else 0,
                    "output_tokens": int(output_tokens) if output_tokens else 0,
                }
            )
        except Exception:
            return

    @staticmethod
    def _is_malformed_response(value: Any) -> bool:
        """Check if a response value is a known malformed non-JSON-object pattern."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped in ("[[ ]]", "[]", "[1]", "[0]") or stripped.startswith("[["):
                return True
        return False

    @staticmethod
    def _is_parse_error(exc: Exception) -> bool:
        """Classify whether ``exc`` is an adapter parse failure (vs timeout/transport).

        Narrowed to JSON-specific markers and ``json.JSONDecodeError`` so that
        broad substrings like ``"expected"``, ``"invalid"``, or ``"decode"`` in
        isolation (e.g. ``ValueError("Invalid API key")``) do NOT trigger the
        parse-error recovery path. This helper is orthogonal to the (now removed)
        hand-rolled salvage cascade — it only routes between two recovery
        behaviours: append a [Parse Error] REPL entry (loop continues) vs append a
        [Timeout] REPL entry (loop continues after a bounded LM timeout).
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

    @staticmethod
    def _extract_completion_from_parse_error(exc: Exception) -> str | None:
        """Pull the raw LM completion text out of an ``AdapterParseError``.

        Delegates to :func:`fleet_rlm.runtime.content.parse_recovery.extract_completion_from_parse_error`,
        which prefers the public ``dspy.AdapterParseError.lm_response`` attribute
        (``dspy/utils/exceptions.py:224-261``) and falls back to scraping the
        UNDOCUMENTED ``"LM Response: "`` message format only as a defensive shim.
        Orthogonal to adapter parsing — used by the echo-back / degenerate-output
        guard.
        """
        return _extract_completion(exc)

    @staticmethod
    def _is_degenerate_response(completion: Any) -> bool:
        """Prompt-shaping guard: detect unusable outputs (e.g. ``{len(doc)}``).

        Delegates to :func:`fleet_rlm.runtime.content.parse_recovery.is_degenerate_response`.
        NOT adapter-parsing logic — DSPy's native ``ChatAdapter`` →
        ``JSONAdapter`` fallback (``dspy/adapters/chat_adapter.py:46,68,87-94``)
        handles parsing. This guard classifies completions for the echo-back /
        degenerate-output budget guard.
        """
        return _is_degenerate(completion)

    @staticmethod
    def _truncate_completion(completion: str) -> str:
        """Truncate the raw completion for echo-back detection (prompt/cost shaping).

        Delegates to :func:`fleet_rlm.runtime.content.parse_recovery.truncate_completion`.
        NOT adapter-parsing logic — bounds the inspection cost for the echo-back
        anomaly (the model replaying ``variables_info`` as its "response").
        """
        return _truncate(completion)

    def _execute_iteration(self, *args: Any, **kwargs: Any) -> Any:
        """Override base RLM iteration to bound action-gen and respect the loop contract.

        Binds a bounded stock ``dspy.LM`` (per-IO timeout, qwen thinking off)
        around ``generate_action`` via ``dspy.settings.context`` — no
        ThreadPoolExecutor. The previous thread-pool wrapper's
        ``__exit__`` join defeated the ``action_timeout`` (the worker can't be
        killed, so a 123.8s LM call ran past the 90s future timeout), and its
        ``return None`` on timeout violated the RLM loop contract
        (``history = result`` → ``history = None`` → the next iteration broke),
        bypassing dspy's ``_extract_fallback`` salvage.

        Contract (per ``dspy.RLM.forward``): return a ``Prediction`` (SUBMIT
        fired → loop exits) or the current ``repl_history`` (loop continues). On
        a single action-gen timeout/transport error, append a ``[Timeout]`` REPL
        entry and return ``repl_history`` so the loop continues and dspy's
        ``_extract_fallback`` can salvage a real answer. After 2 consecutive
        timeouts, raise ``dspy.LMError`` so
        ``EscalatingFleetModule._run_rlm`` can warn + retry with reduced
        context. Never returns ``None``.
        """
        import dspy as _dspy

        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        # Base ``dspy.RLM.forward`` calls ``_execute_iteration`` positionally as
        # ``_execute_iteration(repl, variables, history, iteration, input_args,
        # output_field_names)`` — so ``kwargs`` is empty and the previous
        # ``getattr(self.generate_action, "current_iteration", 0)`` always
        # returned 0 (the attribute is only set when ``_EmittingAction.forward``
        # receives an ``iteration`` kwarg, which never happened because kwargs
        # was empty). Reconstruct the kwargs the base class would have passed to
        # ``generate_action`` so it receives ``variables_info``, ``repl_history``,
        # and ``iteration`` (otherwise it raises ``TypeError``), and read the
        # iteration index from ``args[3]`` so progress events and MLflow spans
        # report the correct iteration number (0, 1, 2, ...).
        iteration = args[3] if len(args) >= 4 else 0
        try:
            iteration = int(iteration)
        except (TypeError, ValueError):
            iteration = 0
        # Keep ``_EmittingAction.current_iteration`` in sync so
        # ``_process_execution_result`` and ``_execute_code`` (which read it)
        # emit the correct iteration on ``rlm_tool_result`` / repl spans.
        try:
            self.generate_action.current_iteration = iteration
        except (AttributeError, TypeError):
            logger.debug("Unable to set generate_action.current_iteration; continuing without sync.")
        except Exception:
            logger.warning(
                "Unexpected error while setting generate_action.current_iteration; continuing.",
                exc_info=True,
            )

        if not kwargs and len(args) >= 4:
            # args layout: (repl, variables, history, iteration, input_args, output_field_names)
            variables = args[1]
            history = args[2]
            try:
                variables_info = [v.format() for v in (variables or [])]
            except Exception:
                variables_info = variables if isinstance(variables, list) else []
            kwargs = {
                "variables_info": variables_info,
                "repl_history": history,
                "iteration": f"{iteration + 1}/{self.max_iterations}",
            }

        # ── P1: Iteration budget guardrail ───────────────────────────────────
        # If the agent has spent too many iterations exploring without
        # submitting, inject a "summarize and SUBMIT now" directive so it
        # produces a final answer instead of burning the rest of the budget.
        if (
            iteration >= _SUMMARY_ITERATION_THRESHOLD
            and iteration < self.max_iterations - 1
            and not getattr(self, "_summary_directive_injected", False)
        ):
            cur_history = self._resolve_repl_history(args, kwargs)
            new_history = self._append_repl_entry(
                cur_history,
                reasoning=(
                    "Iteration budget guardrail: you have spent many iterations exploring. "
                    "Summarize your findings so far and call SUBMIT(response=...) with your "
                    "best answer NOW. Do not continue exploring or running more code."
                ),
                code="# [Budget Guardrail] Summarize findings and SUBMIT immediately.",
                output="",
            )
            if new_history is not None and "repl_history" in kwargs:
                kwargs["repl_history"] = new_history
            self._summary_directive_injected = True
            logger.info(
                "RLM iteration budget guardrail injected at iteration %s",
                iteration,
            )

        timeout = self.action_timeout
        original_generate_action = self.generate_action
        base_lm, config_overrides = self._get_action_lm_config()

        # Emit iteration-start event for real-time progress in the frontend
        self._emit_step(
            {
                "phase": "rlm_iteration",
                "iteration": iteration,
                "text": f"RLM iteration {iteration + 1} starting...",
                "source_type": "rlm_progress",
                "timestamp": time.time(),
            }
        )

        action_result: Any = None
        action_succeeded = False

        def _run_action(**kw: Any) -> Any:
            if config_overrides:
                existing_config = kw.get("config", {})
                kw["config"] = {**config_overrides, **existing_config}
            if base_lm is not None:
                with _dspy.settings.context(lm=base_lm):
                    return original_generate_action(**kw)
            return original_generate_action(**kw)

        with mlflow_child_span(
            "fleet_rlm.rlm_action_generation",
            span_type="LLM",
            attributes={
                "fleet_rlm.tool_name": "rlm_action_generation",
                "fleet_rlm.rlm_iteration": str(iteration),
                "fleet_rlm.rlm_action_timeout_s": str(timeout),
                "fleet_rlm.execution_origin": "dspy_rlm_execute_iteration",
                "fleet_rlm.rlm_bounded_lm": str(bool(config_overrides)),
            },
        ) as span:
            # Emit progress event BEFORE blocking LLM call so the user sees feedback
            # during potentially long action-generation waits (e.g. 90s+).
            _start_time = time.time()
            self._emit_step(
                {
                    "phase": "rlm_action_gen",
                    "iteration": iteration,
                    "status": "started",
                    "text": f"Generating action for iteration {iteration + 1}...",
                    "source_type": "rlm_progress",
                    "timestamp": _start_time,
                }
            )
            try:
                action_result = _run_action(**kwargs)
                action_succeeded = True
                self._consecutive_timeouts = 0
                self._consecutive_parse_errors = 0
                self._emit_step(
                    {
                        "phase": "rlm_action_gen",
                        "iteration": iteration,
                        "status": "completed",
                        "text": f"Action generated in {time.time() - _start_time:.1f}s",
                        "source_type": "rlm_progress",
                        "duration_s": time.time() - _start_time,
                    }
                )
            except Exception as exc:
                self._emit_step(
                    {
                        "phase": "rlm_action_gen",
                        "iteration": iteration,
                        "status": "failed",
                        "text": f"Action generation failed: {exc}",
                        "source_type": "rlm_progress",
                        "error": str(exc),
                    }
                )
                if self._is_parse_error(exc):
                    # ChatAdapter (now the primary adapter) already ran its
                    # native JSONAdapter fallback internally
                    # (``dspy/adapters/chat_adapter.py:46,68,87-94``). If parsing
                    # still fails there is no completion worth salvaging
                    # in-process; respect the loop contract (like the timeout
                    # path): append a [ParseError] REPL entry, return the
                    # updated history so the loop continues and dspy's
                    # ``_extract_fallback`` can salvage, and escalate only after
                    # the consecutive-parse-error cap fires.
                    self._consecutive_parse_errors += 1
                    logger.warning(
                        "RLM action generation parse error (iteration %s, consecutive: %s): %s",
                        iteration,
                        self._consecutive_parse_errors,
                        exc,
                    )
                    if self._consecutive_parse_errors >= self._max_consecutive_parse_errors:
                        # Persistently glitching model: stop burning iterations
                        # and escalate to EscalatingFleetModule.
                        set_mlflow_span_outputs(
                            span,
                            {
                                "status": "parse_error_capped",
                                "iteration": iteration,
                                "consecutive_parse_errors": self._consecutive_parse_errors,
                            },
                        )
                        raise _dspy.LMError(
                            f"RLM action generation parse errors exceeded cap "
                            f"({self._consecutive_parse_errors}) at iteration {iteration}; escalating."
                        )
                    # Truncate raw completion if extracted, to avoid polluting the REPL history
                    # with potentially massive echoed inputs or malformed blocks.
                    raw_completion = self._extract_completion_from_parse_error(exc)
                    if raw_completion:
                        truncated = self._truncate_completion(raw_completion)
                        is_degenerate = self._is_degenerate_response(truncated)
                        if is_degenerate:
                            err_msg = f"[ParseError] Degenerate response or echo-back detected: {truncated[:200]}..."
                        else:
                            err_msg = f"[ParseError] Malformed structured output: {truncated[:200]}..."
                    else:
                        err_msg = f"[ParseError] {str(exc)[:500]}"

                    cur_history = self._resolve_repl_history(args, kwargs)
                    new_history = self._append_repl_entry(
                        cur_history,
                        reasoning="Previous response could not be parsed.",
                        code="# [Parse Error] The adapter could not parse the previous response.",
                        output=err_msg,
                    )
                    if new_history is not None and "repl_history" in kwargs:
                        kwargs["repl_history"] = new_history
                    set_mlflow_span_outputs(
                        span,
                        {"status": "parse_error", "iteration": iteration},
                    )
                    # Contract: return a REPLHistory so the loop continues.
                    return new_history if new_history is not None else cur_history
                elif isinstance(exc, _dspy.LMError):
                    # Real timeout/transport error from the bounded BaseLM (or the
                    # global LM if no bounded LM could be built). Respect the loop
                    # contract: append a [Timeout] entry and return the (updated)
                    # repl_history so the loop continues and dspy's
                    # _extract_fallback can salvage; raise only after repeated
                    # failures. Never return None (would corrupt ``history = result``).
                    self._consecutive_timeouts += 1
                    logger.warning(
                        "RLM action generation failed (%s) (iteration %s, consecutive: %s)",
                        exc,
                        iteration,
                        self._consecutive_timeouts,
                    )
                    cur_history = self._resolve_repl_history(args, kwargs)
                    new_history = self._append_repl_entry(
                        cur_history,
                        reasoning="Action generation failed (timeout or transport error).",
                        output=f"[Timeout] Action generation failed after {timeout}s. Continuing to next iteration. ({exc})",
                    )
                    if new_history is not None and "repl_history" in kwargs:
                        kwargs["repl_history"] = new_history
                    if self._consecutive_timeouts >= self._max_consecutive_timeouts:
                        set_mlflow_span_outputs(
                            span,
                            {"status": "timeout_repeated", "iteration": iteration, "timeout_s": timeout},
                        )
                        raise
                    set_mlflow_span_outputs(
                        span,
                        {"status": "timeout", "iteration": iteration, "timeout_s": timeout},
                    )
                    # Contract: return a REPLHistory so the loop continues.
                    return new_history if new_history is not None else cur_history
                else:
                    raise

            # --- Record per-iteration token usage ---
            if action_succeeded and action_result is not None:
                self._record_iteration_token_usage(iteration)

            if span is not None:
                set_mlflow_span_outputs(
                    span,
                    {
                        "status": "ok" if action_succeeded else "error",
                        "iteration": iteration,
                        "timeout_s": timeout,
                    },
                )

        # If we got a valid action, run it through the base's strip → execute →
        # process pipeline. The base ``_execute_iteration`` would re-call
        # ``generate_action``; we already have the action (generated under the
        # bounded LM), so inline the three steps. Without this the loop exits
        # with a ``Prediction`` that carries only ``reasoning``/``code`` and has
        # no ``trajectory`` → ``has_trajectory=false`` (regression: the override
        # meant to *wrap* action-gen, not *replace* the execute+process half).
        if action_result is not None:
            repl = args[0] if len(args) >= 1 else None
            input_args = args[4] if len(args) >= 5 else {}
            history = args[2] if len(args) >= 3 else kwargs.get("repl_history")
            output_field_names = args[5] if len(args) >= 6 else []
            try:
                code = _strip_code_fences(action_result.code)
            except SyntaxError as e:
                code = action_result.code
                result = f"[Error] {e}"
                return self._process_execution_result(action_result, code, result, history, output_field_names)
            result = self._execute_code(repl, code, input_args)
            return self._process_execution_result(action_result, code, result, history, output_field_names)

        # Action generation failed (parse-error/timeout we couldn't recover
        # from); delegate to the base ``_execute_iteration`` so dspy's
        # ``_extract_fallback`` can still salvage a real answer.
        return super()._execute_iteration(*args, **kwargs)

    def _get_action_lm_config(self) -> tuple[Any | None, dict[str, Any]]:
        """Resolve small/delegate LM and its call-time configuration overrides.

        **P0: Tiered model selection.**  Tries ``DSPY_DELEGATE_LM_SMALL_MODEL``
        first (a cheaper, faster model for the exploration and helper steps that
        dominate action-generation calls).  Falls back to the planner LM
        (``dspy.settings.lm``) when no small delegate is configured.

        Reads the appropriate LM from env / settings and generates configuration
        overrides.
        Returns ``(base_lm, config_overrides)``.
        """
        import dspy as _dspy

        # ── P0: Prefer the small delegate LM for action generation ──────────
        # Exploration steps (the first few iterations that just inspect context)
        # don't need qwen3.7-max; a smaller model handles them in seconds.
        from fleet_rlm.runtime.config import build_lm_config, get_delegate_small_lm_from_env

        base: Any | None = None
        try:
            small_lm = get_delegate_small_lm_from_env()
            if small_lm is not None:
                base = small_lm
                logger.debug("Using small delegate LM for bounded action generation")
        except Exception:
            logger.debug(
                "Failed to load small delegate LM; falling back to default LM.",
                exc_info=True,
            )

        target_lm = base if base is not None else getattr(_dspy.settings, "lm", None)

        # ── P2: Lower max_tokens default ─────────────────────────────────────
        # Most action-generation steps output tiny code snippets (130 tokens in
        # the observed trace).  4096 is excessive; 2048 still allows complex
        # multi-tool calls while signalling the inference server to finish sooner.
        max_tokens = (
            self.action_max_tokens
            if self.action_max_tokens is not None
            else _env_int("FLEET_RLM_ACTION_MAX_TOKENS", 2048)
        )
        timeout = float(self.action_timeout) if self.action_timeout is not None else None
        config_overrides = build_lm_config(
            target_lm,
            max_tokens=max_tokens,
            temperature=0.0,
            timeout=timeout,
        )
        return base, config_overrides

    def _resolve_repl_history(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any | None:
        """Return the current REPLHistory whether passed as a ``repl_history``
        keyword (generate_action call style) or positionally as ``args[2]``
        (base ``dspy.RLM.forward`` calls ``_execute_iteration(repl, variables,
        history, iteration, input_args, output_field_names)``).
        """
        repl_history = kwargs.get("repl_history")
        if repl_history is None and len(args) >= 3:
            repl_history = args[2]
        return repl_history

    def _append_repl_entry(
        self,
        repl_history: Any | None,
        *,
        reasoning: str = "",
        code: str = "",
        output: str = "",
    ) -> Any | None:
        """Return a new ``REPLHistory`` with a ``REPLEntry`` appended.

        Returns the original ``repl_history`` unchanged if it is ``None`` (no
        history yet) so callers can fall back to it.
        """
        from dspy.primitives.repl_types import REPLEntry, REPLHistory

        if repl_history is None:
            return None
        entries = list(getattr(repl_history, "entries", []) or [])
        entries.append(REPLEntry(reasoning=reasoning, code=code, output=output))
        return REPLHistory(
            entries=entries,
            max_output_chars=getattr(repl_history, "max_output_chars", 1500),
        )

    def _process_execution_result(
        self,
        pred: Any,
        code: str,
        result: Any,
        history: Any,
        output_field_names: list[str],
    ) -> Any:
        processed = super()._process_execution_result(pred, code, result, history, output_field_names)
        if not isinstance(processed, dspy.Prediction):
            if isinstance(result, list):
                output = "\n".join(map(str, result))
            else:
                output = str(result) if result else ""
            iteration = getattr(self.generate_action, "current_iteration", 0)
            self._emit_step(
                {
                    "phase": "rlm_tool_result",
                    "iteration": iteration,
                    "output": output,
                    "observation": output,
                    "tool_name": "repl_execute",
                }
            )
        return processed

    def _execute_code(self, repl: Any, code: str, input_args: dict[str, Any]) -> Any:
        from fleet_rlm.integrations.observability.mlflow_context import (
            _bounded_value,
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        iteration = getattr(self.generate_action, "current_iteration", 0)

        # ── P2: REPL output cache ───────────────────────────────────────────
        # Cache by code hash so identical code blocks return the cached result
        # without a sandbox round-trip. Only cache successful (non-error)
        # results that don't contain [Error] prefixes. Skip caching for code
        # that contains SUBMIT (terminal) or has side effects.
        cache_key = ""
        if _REPL_OUTPUT_CACHE_ENABLED:
            import hashlib

            cache_key = hashlib.sha256(code.encode("utf-8")).hexdigest()
            if cache_key in self._repl_output_cache and "SUBMIT" not in code:
                logger.debug("REPL output cache hit for code hash %s (iteration %s)", cache_key[:8], iteration)
                return self._repl_output_cache[cache_key]

        with mlflow_child_span(
            "fleet_rlm.rlm_repl_execute",
            span_type="TOOL",
            attributes={
                "fleet_rlm.tool_name": "repl_execute",
                "fleet_rlm.rlm_iteration": str(iteration),
                "fleet_rlm.execution_origin": "dspy_rlm_execute_code",
            },
            inputs={
                "tool_name": "repl_execute",
                "iteration": iteration,
                "code": _bounded_value(code),
                "variable_names": sorted(str(key) for key in input_args),
            },
        ) as span:
            result = super()._execute_code(repl, code, input_args)
            failed = isinstance(result, str) and result.startswith("[Error]")
            set_mlflow_span_outputs(
                span,
                {
                    "status": "error" if failed else "ok",
                    "result": _bounded_value(result),
                },
            )
            if failed and span is not None:
                set_status = getattr(span, "set_status", None)
                if callable(set_status):
                    set_status("ERROR")
            # P2: Cache successful, non-terminal results
            if (
                _REPL_OUTPUT_CACHE_ENABLED
                and not failed
                and "SUBMIT" not in code
                and isinstance(result, str)
                and cache_key
            ):
                self._repl_output_cache[cache_key] = result
            return result

    # ── P1: Cached serializable variable preparation ───────────────────────
    # _prepared_serializable_cache stores the result of the last
    # _prepare_serializable_vars call keyed by (frozenset of input arg names).
    # Large objects like WorkspaceContext and ActiveSkills are expensive to
    # serialize (4.3s in the observed trace) but immutable across iterations.
    # NOTE: The cache is a per-instance dict initialized in __init__ (not a
    # class-level attribute) so two RLM instances with the same variable names
    # but different content do not share cached data. It is cleared at the
    # start of each forward() call to prevent stale data across turns.

    @staticmethod
    def _build_context_index(document_text: str) -> dict[str, Any]:
        """Pre-parse a large ``document_text`` into a structured index.

        Returns a dict with:
        - ``sections``: list of ``{header, start, end, char_count}`` for each
          markdown header section.
        - ``file_paths``: list of all file-like paths found in the text.
        - ``total_chars``: total document length.
        - ``total_sections``: number of sections.
        - ``code_blocks``: count of fenced code blocks.

        This index is injected as the ``context_index`` REPL variable so the
        agent can jump directly to the relevant section instead of discovering
        the layout through 15+ iterations of trial-and-error regex.
        """
        import re

        if not document_text or not _CONTEXT_PREPARSE_ENABLED:
            return {}

        if len(document_text) < _CONTEXT_PREPARSE_THRESHOLD:
            return {}

        sections: list[dict[str, Any]] = []
        header_pattern = re.compile(r"^#{1,3} .+", re.MULTILINE)
        matches = list(header_pattern.finditer(document_text))
        if matches:
            if matches[0].start() > 0:
                preamble = document_text[: matches[0].start()].strip()
                if preamble:
                    sections.append(
                        {
                            "header": "(preamble)",
                            "start": 0,
                            "end": matches[0].start(),
                            "char_count": len(preamble),
                        }
                    )
            for i, match in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(document_text)
                header_line = match.group(0).strip()
                sections.append(
                    {
                        "header": header_line,
                        "start": match.start(),
                        "end": end,
                        "char_count": end - match.start(),
                    }
                )
        else:
            sections.append(
                {
                    "header": "(full document)",
                    "start": 0,
                    "end": len(document_text),
                    "char_count": len(document_text),
                }
            )

        # Extract file-like paths
        file_paths = sorted(set(re.findall(r"[\w/]+\.\w{1,10}", document_text)))
        if len(file_paths) > 500:
            file_paths = file_paths[:500]

        # Count fenced code blocks
        code_blocks = len(re.findall(r"```", document_text)) // 2

        return {
            "sections": sections,
            "file_paths": file_paths,
            "total_chars": len(document_text),
            "total_sections": len(sections),
            "code_blocks": code_blocks,
        }

    def _prepare_serializable_vars(self, input_args: dict[str, Any], repl: Any) -> dict[str, Any]:
        from dspy.predict.rlm import SandboxSerializable

        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        serializable_names = sorted(
            str(name) for name, value in input_args.items() if isinstance(value, SandboxSerializable)
        )
        if not serializable_names:
            return super()._prepare_serializable_vars(input_args, repl)

        # ── P1: Cache hit — skip re-serialization for immutable objects ────
        # Keyed by ``(name, id(value))`` tuples so a corrective retry inside
        # the same ``_run_rlm`` invocation (same SandboxSerializable binding,
        # same ``id()``) hits the cache, while a fresh turn that rebinds the
        # same names to different objects misses the cache and re-serializes.
        # The reset is owned by ``EscalatingFleetModule._run_rlm`` (cleared
        # once per turn on the chosen RLM instance) so retries within a turn
        # reuse the serialized variables across primary + corrective calls.
        cache_key = frozenset((name, id(input_args[name])) for name in serializable_names)
        if cache_key in self._prepared_serializable_cache:
            cached = self._prepared_serializable_cache[cache_key]
            logger.debug("rlm_prepare_variables: cache hit for %s", serializable_names)
            return dict(cached)

        with mlflow_child_span(
            "fleet_rlm.rlm_prepare_variables",
            span_type="CHAIN",
            attributes={
                "fleet_rlm.serializable_variable_count": str(len(serializable_names)),
                "fleet_rlm.execution_origin": "dspy_rlm_prepare_serializable_vars",
                "fleet_rlm.cache_hit": "false",
            },
            inputs={"serializable_variables": serializable_names},
        ) as span:
            regular_args = super()._prepare_serializable_vars(input_args, repl)

            # ── P1: Pre-parse large document_text and inject context_index ──
            # The agent no longer needs 15+ iterations to discover document
            # layout via trial-and-error regex. The index provides section
            # offsets, file paths, and code block counts so the agent can jump
            # directly to the relevant section.
            context_value = input_args.get("context")
            if context_value is not None:
                doc_text = getattr(context_value, "document_text", None)
                if not doc_text and isinstance(context_value, dict):
                    doc_text = context_value.get("document_text", "")
                if doc_text and len(doc_text) >= _CONTEXT_PREPARSE_THRESHOLD:
                    index = self._build_context_index(doc_text)
                    if index and repl is not None:
                        # Inject context_index as a regular (non-serializable)
                        # REPL variable so the agent can use it immediately.
                        regular_args["context_index"] = index
                        logger.info(
                            "context_index pre-parsed: %s sections, %s file_paths, %s code_blocks",
                            index.get("total_sections", 0),
                            len(index.get("file_paths", [])),
                            index.get("code_blocks", 0),
                        )

            set_mlflow_span_outputs(
                span,
                {
                    "regular_variable_count": len(regular_args),
                    "regular_variables": sorted(str(key) for key in regular_args),
                },
            )
            # Cache for subsequent iterations
            self._prepared_serializable_cache[cache_key] = dict(regular_args)
            return regular_args

    def _record_trajectory_spans(self, result: Any) -> None:
        try:
            from fleet_rlm.integrations.observability.mlflow_context import record_rlm_trajectory_spans

            record_rlm_trajectory_spans(getattr(result, "trajectory", None))
        except Exception:
            return

    def forward(self, **input_args: Any) -> dspy.Prediction:
        # The cache reset is owned by EscalatingFleetModule._run_rlm (cleared
        # once per turn before the execution loop) so corrective retries within
        # a turn reuse the serialized variables. Do NOT clear here.
        # Validate critical variables are present
        variables_info = input_args.get("variables_info")
        if not variables_info or not isinstance(variables_info, str) or len(variables_info) < 50:
            logger.error(
                "_StreamingRLM.forward: variables_info is malformed or empty (len=%s). "
                "This may cause the model to report 'No input data provided'. "
                "Preview: %s",
                len(variables_info) if isinstance(variables_info, str) else "N/A",
                str(variables_info)[:200] if variables_info else "None",
            )

        with self._structured_action_context():
            result = super().forward(**input_args)

        # Always capture response preview for traceOutputs
        try:
            from fleet_rlm.integrations.observability.mlflow_runtime import update_current_mlflow_trace

            trajectory = getattr(result, "trajectory", None)
            if trajectory:
                # Capture preview from trajectory
                if isinstance(trajectory, list) and trajectory:
                    last_action = trajectory[-1]
                    reasoning = (
                        last_action.get("reasoning") or last_action.get("thought") or ""
                        if isinstance(last_action, dict)
                        else getattr(last_action, "reasoning", "") or getattr(last_action, "thought", "") or ""
                    )
                    code = (
                        last_action.get("code") or ""
                        if isinstance(last_action, dict)
                        else getattr(last_action, "code", "") or ""
                    )
                    preview = (
                        f"[RLM Action] {reasoning[:150]}\n{code[:200]}"
                        if code
                        else f"[RLM Reasoning] {reasoning[:300]}"
                    )
                else:
                    preview = f"[RLM Trajectory: {type(trajectory).__name__}]"
            else:
                # Fallback: capture action output as response preview
                reasoning = getattr(result, "reasoning", "") or ""
                code = getattr(result, "code", "") or ""
                if reasoning or code:
                    preview = (
                        f"[RLM Action] {reasoning[:150]}\n{code[:200]}"
                        if code
                        else f"[RLM Reasoning] {reasoning[:300]}"
                    )
                else:
                    preview = "[No response captured - RLM iteration incomplete]"

            update_current_mlflow_trace(response_preview=preview)
        except Exception:
            logger.debug("Failed to capture RLM response preview in trace", exc_info=True)

        self._record_trajectory_spans(result)
        return result

    async def aforward(self, **input_args: Any) -> dspy.Prediction:
        with self._structured_action_context():
            result = await super().aforward(**input_args)
        self._record_trajectory_spans(result)
        return result


class _NoCallbackRLM(_StreamingRLM):
    """RLM variant for REPL-only tasks where host semantic callbacks are disabled."""

    def _build_signatures(self) -> tuple[Any, Any]:
        action_sig, extract_sig = super()._build_signatures()
        instructions = str(action_sig.instructions)
        instructions = instructions.replace(
            "- `llm_query(prompt)` - query a sub-LLM (~500K char capacity) for semantic analysis\n",
            "",
        ).replace(
            "- `llm_query_batched(prompts)` - query multiple prompts concurrently (much faster for multiple queries)\n",
            "",
        )
        instructions = instructions.replace(
            "4. USE llm_query FOR SEMANTICS - String matching finds WHERE things are; "
            "llm_query understands WHAT things mean.",
            "4. USE PYTHON INSPECTION - Extract headings, links, counts, samples, and sections with code; "
            "semantic callbacks are disabled for this run.",
        )
        instructions = instructions.replace(
            f"You have max {self.max_llm_calls} sub-LLM calls. When done, call SUBMIT() with your output.",
            "Semantic callbacks are disabled. When done, call SUBMIT() with your output.",
        )
        return action_sig.with_instructions(instructions), extract_sig

    def _make_llm_tools(self, max_workers: int = 8) -> dict[str, Any]:
        _ = max_workers
        return {}

    def _repl_only_context(self) -> Any:
        """No-op adapter context for the REPL-only RLM path.

        Lets the DSPy-default ChatAdapter (with native JSONAdapter fallback,
        ``dspy/adapters/chat_adapter.py:46,68,87-94``) handle structured output
        instead of forcing ``JSONAdapter`` as primary.
        """
        return nullcontext()

    def forward(self, **input_args: Any) -> dspy.Prediction:
        interpreter = getattr(self, "_interpreter", None)
        previous = getattr(interpreter, "semantic_callbacks_enabled", True)
        try:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", False)
            with self._repl_only_context():
                return super().forward(**input_args)
        finally:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", previous)

    async def aforward(self, **input_args: Any) -> dspy.Prediction:
        interpreter = getattr(self, "_interpreter", None)
        previous = getattr(interpreter, "semantic_callbacks_enabled", True)
        try:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", False)
            with self._repl_only_context():
                return await super().aforward(**input_args)
        finally:
            if interpreter is not None:
                setattr(interpreter, "semantic_callbacks_enabled", previous)


def create_runtime_rlm(
    *,
    signature: type[dspy.Signature],
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    action_max_tokens: int | None = None,
    action_timeout: int | None = None,
    verbose: bool,
    tools: list[Any] | None = None,
    sub_lm: dspy.LM | None = None,
    include_llm_tools: bool = True,
) -> dspy.Module:
    """Create a canonical RLM instance for a runtime signature."""

    kwargs: dict[str, Any] = {
        "signature": signature,
        "interpreter": interpreter,
        "max_iterations": max_iterations,
        "max_llm_calls": max_llm_calls,
        "verbose": verbose,
    }
    if action_max_tokens is not None:
        kwargs["action_max_tokens"] = action_max_tokens
    if action_timeout is not None:
        kwargs["action_timeout"] = action_timeout
    if max_output_chars is not None:
        kwargs["max_output_chars"] = max_output_chars
    if tools is not None:
        kwargs["tools"] = tools
    if sub_lm is not None:
        kwargs["sub_lm"] = sub_lm

    rlm_cls: type[Any]
    if not include_llm_tools:
        rlm_cls = _NoCallbackRLM
    else:
        rlm_cls = _StreamingRLM

    return rlm_cls(**kwargs)


def build_recursive_subquery_rlm(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    max_output_chars: int | None = None,
    action_max_tokens: int | None = None,
    verbose: bool,
    sub_lm: dspy.LM | None = None,
) -> dspy.Module:
    """Build the canonical recursive child-query RLM."""

    from fleet_rlm.runtime.agent.signatures import RecursiveSubQuerySignature

    return create_runtime_rlm(
        signature=RecursiveSubQuerySignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        action_max_tokens=action_max_tokens,
        verbose=verbose,
        sub_lm=sub_lm,
    )


@dataclass(frozen=True)
class RuntimeModuleBuildConfig:
    """Shared constructor parameters for runtime-module RLMs."""

    interpreter: Any
    max_iterations: int
    max_llm_calls: int
    verbose: bool
    max_output_chars: int | None = None
    action_max_tokens: int | None = None
    action_timeout: int | None = None
    sub_lm: dspy.LM | None = None


def build_runtime_module_config(
    *,
    interpreter: Any,
    max_iterations: int,
    max_llm_calls: int,
    verbose: bool,
    max_output_chars: int | None = None,
    action_max_tokens: int | None = None,
    action_timeout: int | None = None,
    sub_lm: dspy.LM | None = None,
) -> RuntimeModuleBuildConfig:
    """Construct a ``RuntimeModuleBuildConfig`` from keyword arguments."""
    return RuntimeModuleBuildConfig(
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
        max_output_chars=max_output_chars,
        action_max_tokens=action_max_tokens,
        action_timeout=action_timeout,
        sub_lm=sub_lm,
    )


def _create_configured_runtime_rlm(
    config: RuntimeModuleBuildConfig,
    *,
    signature: type[dspy.Signature],
    tools: list[Any] | None = None,
) -> dspy.Module:
    """Create a runtime RLM from a shared build config."""
    return create_runtime_rlm(
        signature=signature,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        max_output_chars=config.max_output_chars,
        action_max_tokens=config.action_max_tokens,
        action_timeout=config.action_timeout,
        verbose=config.verbose,
        tools=tools,
        sub_lm=config.sub_lm,
    )


__all__ = [
    "RuntimeModuleBuildConfig",
    "VARIABLE_MODE_MAX_OUTPUT_CHARS",
    "VARIABLE_MODE_THRESHOLD",
    "_create_configured_runtime_rlm",
    "build_recursive_subquery_rlm",
    "build_runtime_module_config",
    "create_runtime_rlm",
    "interpreter_delegation_tools",
]
