"""LLM query mixin and helpers for interpreter-backed RLM flows.

Provides :class:`LLMQueryMixin` with built-in RLM tools for recursive LLM calls
(``llm_query``, ``llm_query_batched``) and true-RLM symbolic recursion primitives
(``sub_rlm``, ``sub_rlm_batched``).
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from typing import Any

import dspy

from fleet_rlm.runtime.modules.factory import build_recursive_subquery_rlm
from fleet_rlm.utils.marker_search import contains_marker

logger = logging.getLogger(__name__)

_BROKER_ERROR_MARKER = "Broker server failed to start"


def _env_int(name: str, default: int) -> int:
    """Read an int env var, returning ``default`` when unset/invalid."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Separate executors for llm_query_batched and sub_rlm_batched to avoid
# deadlocks under nested usage: sub_rlm children may call llm_query_batched
# from within sub_rlm_batched threads, so they must not share the same pool.
_LLM_BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm_batch")
_SUB_RLM_BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sub_rlm_batch")
_LLM_QUERY_BATCH_WINDOW = max(1, min(8, _env_int("FLEET_RLM_LLM_QUERY_BATCH_WINDOW", 4)))


# Phase 7: child conversation snapshot configuration
_CHILD_HISTORY_MAX_TURNS = 2
_CHILD_HISTORY_MAX_CHARS = 2000

# Simple redaction patterns for child history snapshots
_SENSITIVE_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***REDACTED***"),
    (re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s]+", re.IGNORECASE), r"\1***REDACTED***"),
    (
        re.compile(
            r"((?:api[_-]?key|token|secret|password)\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,}\]]+)", re.IGNORECASE
        ),
        r"\1***REDACTED***",
    ),
)


def _redact_sensitive(text: str) -> str:
    """Replace API keys and other sensitive tokens with redaction markers."""
    redacted = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _build_child_history_snapshot(interpreter: Any) -> str:
    """Build a bounded, redacted conversation snapshot for recursive children.

    Phase 7: Children receive a fresh REPL (per reference) but need explicit
    conversation continuity. This function extracts the last N turns from the
    parent runtime's history, redacts sensitive values, and bounds the size.

    Args:
        interpreter: The parent interpreter with a runtime reference.

    Returns:
        A bounded, redacted conversation snapshot string.
    """
    runtime = getattr(interpreter, "agent_runtime", None)
    if runtime is None:
        return ""

    history = getattr(runtime, "history", None)
    if history is None:
        return ""

    messages = list(getattr(history, "messages", []) or [])
    if not messages:
        return ""

    # Take the last N turns
    recent_messages = messages[-_CHILD_HISTORY_MAX_TURNS:] if len(messages) > _CHILD_HISTORY_MAX_TURNS else messages

    # Format each turn
    turn_parts = []
    for msg in recent_messages:
        if isinstance(msg, dict):
            user_msg = str(msg.get("user_message", ""))
            response = str(msg.get("response", ""))
            if user_msg or response:
                turn_parts.append(f"User: {user_msg}\nAssistant: {response}")

    snapshot = "\n\n".join(turn_parts)
    if not snapshot:
        return ""

    # Redact sensitive values
    snapshot = _redact_sensitive(snapshot)

    # Truncate to max chars
    if len(snapshot) > _CHILD_HISTORY_MAX_CHARS:
        snapshot = snapshot[:_CHILD_HISTORY_MAX_CHARS] + "...[truncated]"

    return snapshot


class LLMQueryMixin:
    """Mixin providing LLM query tools for recursive LLM calls.

    This mixin implements the RLM pattern where sandboxed code can call
    sub-LLMs for semantic tasks while the parent LLM handles orchestration.

    Attributes Required on Host Class:
        sub_lm: Optional LM for llm_query/llm_query_batched calls.
        max_llm_calls: Maximum number of sub-LLM calls allowed per session.
        llm_call_timeout: Timeout in seconds for individual LLM calls.
        _llm_call_count: Counter for tracking LLM calls.
        _llm_call_lock: Thread lock for counter synchronization.
        _sub_lm_executor: ThreadPoolExecutor for LLM calls.
        _sub_lm_executor_lock: Lock for executor creation.

    Methods:
        llm_query: Query a sub-LLM with a single prompt.
        llm_query_batched: Query a sub-LLM with multiple prompts concurrently.
        _query_sub_lm: Internal method to execute a single LLM query.
        _check_and_increment_llm_calls: Validate and increment call counter.
    """

    # These attributes are provided by the host class
    sub_lm: dspy.LM | None
    max_llm_calls: int
    llm_call_timeout: int
    _llm_call_count: int
    _llm_call_lock: threading.Lock
    _sub_lm_executor: ThreadPoolExecutor | None
    _sub_lm_executor_lock: threading.Lock
    _sub_lm_auth_failed: bool  # Fail-fast flag for 401/Unauthorized errors
    _sub_lm_auth_error: str | None

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
        """Create a child interpreter — implemented by the host class."""

    def _get_sub_lm_config(self, base: Any) -> dict[str, Any]:
        """Return a stateless configuration dictionary for overrides.

        Mirrors ``_get_action_lm_config`` so sub-LLM calls get a per-IO timeout
        (the future-timeout in ``_query_sub_lm`` cannot kill the running worker
        thread), a ``max_tokens`` cap, and qwen extended thinking disabled.
        """
        from fleet_rlm.runtime.config import build_lm_config

        return build_lm_config(
            base,
            max_tokens=_env_int("FLEET_RLM_LLM_QUERY_MAX_TOKENS", 4096),
            temperature=0.0,
            timeout=float(self.llm_call_timeout),
        )

    def _check_and_increment_llm_calls(self, n: int = 1) -> None:
        """Check and increment the LLM call counter.

        Args:
            n: Number of calls to add (default: 1 for single query,
               len(prompts) for batched queries).

        Raises:
            RuntimeError: If the call would exceed max_llm_calls limit.
        """
        with self._llm_call_lock:
            if self._llm_call_count + n > self.max_llm_calls:
                raise RuntimeError(
                    f"LLM call limit exceeded: {self._llm_call_count} + {n} > {self.max_llm_calls}. "
                    f"Use Python code for aggregation instead of making more LLM calls."
                )
            self._llm_call_count += n

    def _decrement_llm_calls(self, n: int) -> None:
        """Release previously reserved LLM-call budget for skipped work."""
        if n <= 0:
            return
        with self._llm_call_lock:
            self._llm_call_count = max(0, self._llm_call_count - n)

    def _resolve_llm_query_target(self) -> tuple[Any | None, str, str]:
        """Resolve the LM used by ``llm_query*`` without mutating DSPy state."""
        if self.sub_lm is not None:
            target = self.sub_lm
            source = "sub_lm"
        else:
            target = dspy.settings.lm
            source = "dspy_settings_lm" if target is not None else "missing"
        model = str(getattr(target, "model", "?")) if target is not None else "?"
        return target, source, model

    def _missing_lm_error(self, *, tool_name: str) -> RuntimeError:
        return RuntimeError(
            f"No LM configured for {tool_name}. Configure an active delegate LLM profile, "
            "pass sub_lm to the active interpreter, or set DSPY_DELEGATE_LM_MODEL plus "
            "DSPY_DELEGATE_LM_API_KEY. If no delegate is configured, ensure the planner "
            "LM is available through the active LLM profile or DSPY_LM_MODEL plus "
            "DSPY_LLM_API_KEY/DSPY_LM_API_KEY."
        )

    def _llm_auth_fail_fast_error(self, *, tool_name: str) -> RuntimeError:
        previous = getattr(self, "_sub_lm_auth_error", None)
        suffix = f" Previous provider error: {previous}" if previous else ""
        return RuntimeError(
            f"Sub-LM authentication previously failed; {tool_name} is disabled for this session. "
            "Check the active delegate LLM profile, delegate API key, provider/model mapping, "
            "or DSPY_DELEGATE_LM_MODEL/DSPY_DELEGATE_LM_API_KEY. If Fleet-RLM is using the "
            "planner fallback, check the active planner profile or DSPY_LM_MODEL and "
            f"DSPY_LLM_API_KEY/DSPY_LM_API_KEY.{suffix}"
        )

    def _raise_if_sub_lm_auth_failed(self, *, tool_name: str) -> None:
        if getattr(self, "_sub_lm_auth_failed", False):
            raise self._llm_auth_fail_fast_error(tool_name=tool_name)

    def _provider_error_detail(self, exc: BaseException) -> dict[str, str]:
        raw = _redact_sensitive(str(exc))
        if len(raw) > 1_000:
            raw = f"{raw[:1_000]}...[truncated]"
        lower = raw.lower()
        status = "401" if "401" in lower else "unauthorized" if "unauthorized" in lower else "unknown"
        return {
            "error_class": type(exc).__name__,
            "provider_status": status,
            "message": raw,
        }

    def _is_auth_failure(self, exc: BaseException) -> bool:
        detail = self._provider_error_detail(exc)
        return detail["provider_status"] in {"401", "unauthorized"}

    def _mark_sub_lm_auth_failed(self, exc: BaseException) -> dict[str, str]:
        detail = self._provider_error_detail(exc)
        self._sub_lm_auth_failed = True
        self._sub_lm_auth_error = (
            f"{detail['error_class']} provider_status={detail['provider_status']}: {detail['message']}"
        )
        logger.warning(
            "Sub-LM auth failed; disabling llm_query for this session. error_class=%s provider_status=%s",
            detail["error_class"],
            detail["provider_status"],
        )
        return detail

    def _record_blocked_llm_query_span(
        self,
        *,
        tool_name: str,
        prompt_count: int,
        skipped_calls: int,
        reason: str,
        target_source: str,
        target_model: str,
    ) -> None:
        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        with mlflow_child_span(
            f"fleet_rlm.{tool_name}",
            span_type="CHAIN" if tool_name.endswith("_batched") else "LLM",
            attributes={
                "fleet_rlm.tool_name": tool_name,
                "fleet_rlm.llm_query_target_source": target_source,
                "fleet_rlm.sub_lm_model": target_model,
                "fleet_rlm.auth_preflight_result": reason,
                "fleet_rlm.auth_fail_fast_state": str(getattr(self, "_sub_lm_auth_failed", False)).lower(),
                "fleet_rlm.batch_prompt_count": str(prompt_count),
                "fleet_rlm.skipped_calls_due_to_auth_fail_fast": str(skipped_calls),
            },
            inputs={"prompt_count": prompt_count},
        ) as span:
            set_mlflow_span_outputs(
                span,
                {
                    "status": "error",
                    "error_kind": reason,
                    "skipped_calls_due_to_auth_fail_fast": skipped_calls,
                },
            )
            if span is not None:
                set_status = getattr(span, "set_status", None)
                if callable(set_status):
                    set_status("ERROR")

    def _query_sub_lm(
        self,
        prompt: str,
        *,
        target_lm: Any | None = None,
        target_source: str | None = None,
        target_model: str | None = None,
    ) -> str:
        """Query the sub-LM with a prompt string.

        Args:
            prompt: The prompt to send to the sub-LM.

        Returns:
            The response text from the sub-LM.

        Raises:
            RuntimeError: If no LM is configured or if the call times out.
        """
        # Fail-fast: if a previous call got 401 Unauthorized, don't retry.
        self._raise_if_sub_lm_auth_failed(tool_name="llm_query")

        if target_lm is None or target_source is None or target_model is None:
            target_lm, target_source, target_model = self._resolve_llm_query_target()
        if target_lm is None:
            raise self._missing_lm_error(tool_name="llm_query")

        temp_target_lm = target_lm
        config_overrides = self._get_sub_lm_config(temp_target_lm) if temp_target_lm is not None else {}
        bounded = bool(config_overrides)
        sub_lm_model = target_model

        from fleet_rlm.integrations.observability.mlflow_context import (
            _bounded_value,
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        # Resolve target_lm in parent thread to inherit thread-locals safely.
        resolved_lm = target_lm

        # Execute LM call with timeout to prevent hangs.
        def _execute_lm(lm: Any) -> str:
            call_overrides = self._get_sub_lm_config(lm)
            response = lm(prompt, **call_overrides)
            if isinstance(response, list) and response:
                item = response[0]
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
                return str(item)
            return str(response)

        # Reuse an executor with modest concurrency to avoid creating unbounded
        # threads when repeated calls time out, while not serializing all calls.
        with self._sub_lm_executor_lock:
            if self._sub_lm_executor is None:
                self._sub_lm_executor = ThreadPoolExecutor(max_workers=min(8, max(1, self.max_llm_calls)))
            executor = self._sub_lm_executor

        ctx = contextvars.copy_context()
        with mlflow_child_span(
            "fleet_rlm.llm_query",
            span_type="LLM",
            attributes={
                "fleet_rlm.tool_name": "llm_query",
                "fleet_rlm.prompt_chars": str(len(prompt)),
                "fleet_rlm.llm_call_timeout_s": str(self.llm_call_timeout),
                "fleet_rlm.bounded": str(bounded),
                "fleet_rlm.sub_lm_model": sub_lm_model,
                "fleet_rlm.llm_query_target_source": target_source,
                "fleet_rlm.max_llm_calls": str(getattr(self, "max_llm_calls", "")),
                "fleet_rlm.llm_call_count": str(getattr(self, "_llm_call_count", "")),
                "fleet_rlm.llm_calls_remaining": str(max(0, self.max_llm_calls - self._llm_call_count)),
                "fleet_rlm.auth_preflight_result": "ok",
                "fleet_rlm.auth_fail_fast_state": str(getattr(self, "_sub_lm_auth_failed", False)).lower(),
            },
            inputs={"prompt_chars": len(prompt), "prompt_preview": _bounded_value(prompt, limit=1_000)},
        ) as span:
            future = executor.submit(ctx.run, _execute_lm, resolved_lm)
            try:
                result = future.result(timeout=self.llm_call_timeout)
                text = result if isinstance(result, str) else str(result)
                set_mlflow_span_outputs(
                    span,
                    {
                        "status": "ok",
                        "response_chars": len(text),
                        "response_preview": _bounded_value(text, limit=1_000),
                    },
                )
                return text
            except FutureTimeoutError as exc:
                future.cancel()
                # Running threads cannot be cancelled; let the thread run in the background
                # but do not tear down/discard the executor as that crashes concurrent batches.
                if span is not None:
                    set_status = getattr(span, "set_status", None)
                    if callable(set_status):
                        set_status("ERROR")
                    set_mlflow_span_outputs(
                        span,
                        {
                            "status": "error",
                            "error_kind": "timeout",
                            "auth_failure": False,
                        },
                    )
                raise RuntimeError(
                    f"LLM call timed out after {self.llm_call_timeout}s. "
                    "Consider increasing llm_call_timeout or checking API connectivity."
                ) from exc
            except Exception as exc:
                # Fail-fast on 401 Unauthorized: set flag to prevent retry storms.
                detail = self._provider_error_detail(exc)
                auth_failure = self._is_auth_failure(exc)
                if auth_failure:
                    detail = self._mark_sub_lm_auth_failed(exc)
                if span is not None:
                    set_status = getattr(span, "set_status", None)
                    if callable(set_status):
                        set_status("ERROR")
                    set_mlflow_span_outputs(
                        span,
                        {
                            "status": "error",
                            "error_kind": "auth_failure" if auth_failure else "provider_error",
                            "auth_failure": auth_failure,
                            "provider_error_class": detail["error_class"],
                            "provider_status": detail["provider_status"],
                            "provider_error": detail["message"],
                        },
                    )
                if auth_failure:
                    raise RuntimeError(
                        "Sub-LM authentication failed while executing llm_query. "
                        "Check the active delegate LLM profile/API key/provider mapping or "
                        "DSPY_DELEGATE_LM_MODEL/DSPY_DELEGATE_LM_API_KEY. "
                        f"Provider error: {detail['error_class']}: {detail['message']}"
                    ) from exc
                raise

    def llm_query(self, prompt: str, context: str = "") -> str:
        """Query a sub-LLM for semantic analysis.

        This is a built-in RLM tool that allows sandboxed code to make
        recursive LLM calls. Each call counts against max_llm_calls.

        Args:
            prompt: The prompt to send to the sub-LLM.
            context: Optional supporting context prepended to the prompt
                (e.g. a document slice). Pass workspace content explicitly:
                ``llm_query("summarise", context['document_text'][:50_000])``.
                The host does NOT auto-include sandbox ``context`` — without
                this argument the sub-LLM sees only ``prompt``.

        Returns:
            The response text from the sub-LLM.

        Raises:
            ValueError: If prompt is empty.
            RuntimeError: If max_llm_calls would be exceeded.

        Example:
            >>> result = llm_query("Summarize this text in one sentence.")
            >>> result = llm_query("What does this code do?", context=snippet)
        """
        if not prompt:
            raise ValueError("prompt cannot be empty")
        target_lm, target_source, target_model = self._resolve_llm_query_target()
        if getattr(self, "_sub_lm_auth_failed", False):
            self._record_blocked_llm_query_span(
                tool_name="llm_query",
                prompt_count=1,
                skipped_calls=1,
                reason="auth_fail_fast",
                target_source=target_source,
                target_model=target_model,
            )
            raise self._llm_auth_fail_fast_error(tool_name="llm_query")
        if target_lm is None:
            self._record_blocked_llm_query_span(
                tool_name="llm_query",
                prompt_count=1,
                skipped_calls=1,
                reason="missing_lm",
                target_source=target_source,
                target_model=target_model,
            )
            raise self._missing_lm_error(tool_name="llm_query")
        self._check_and_increment_llm_calls(1)
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        return self._query_sub_lm(
            full_prompt,
            target_lm=target_lm,
            target_source=target_source,
            target_model=target_model,
        )

    def llm_query_batched(self, prompts: list[str], context: str = "") -> list[str]:
        """Query the sub-LLM with multiple prompts concurrently.

        This is a built-in RLM tool for making multiple LLM calls in parallel.
        Each prompt counts against max_llm_calls.

        Args:
            prompts: List of prompts to send to the sub-LLM.
            context: Optional supporting context prepended to every prompt
                (mirrors ``sub_rlm_batched``). The host does NOT auto-include
                sandbox ``context`` — pass it explicitly when the sub-LLM needs
                workspace content.

        Returns:
            List of response texts, in the same order as prompts.

        Raises:
            RuntimeError: If max_llm_calls would be exceeded, or if any
                batched query fails.

        Example:
            >>> prompts = ["Summarize A", "Summarize B", "Summarize C"]
            >>> results = llm_query_batched(prompts)
        """
        if not prompts:
            return []
        target_lm, target_source, target_model = self._resolve_llm_query_target()
        if getattr(self, "_sub_lm_auth_failed", False):
            self._record_blocked_llm_query_span(
                tool_name="llm_query_batched",
                prompt_count=len(prompts),
                skipped_calls=len(prompts),
                reason="auth_fail_fast",
                target_source=target_source,
                target_model=target_model,
            )
            raise self._llm_auth_fail_fast_error(tool_name="llm_query_batched")
        if target_lm is None:
            self._record_blocked_llm_query_span(
                tool_name="llm_query_batched",
                prompt_count=len(prompts),
                skipped_calls=len(prompts),
                reason="missing_lm",
                target_source=target_source,
                target_model=target_model,
            )
            raise self._missing_lm_error(tool_name="llm_query_batched")
        self._check_and_increment_llm_calls(len(prompts))
        if context:
            prompts = [f"{context}\n\n{p}" if p else p for p in prompts]

        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []
        submitted_count = 0
        skipped_due_to_auth = 0

        with mlflow_child_span(
            "fleet_rlm.llm_query_batched",
            span_type="CHAIN",
            attributes={
                "fleet_rlm.tool_name": "llm_query_batched",
                "fleet_rlm.prompt_count": str(len(prompts)),
                "fleet_rlm.batch_prompt_count": str(len(prompts)),
                "fleet_rlm.llm_query_target_source": target_source,
                "fleet_rlm.sub_lm_model": target_model,
                "fleet_rlm.max_llm_calls": str(getattr(self, "max_llm_calls", "")),
                "fleet_rlm.llm_call_count": str(getattr(self, "_llm_call_count", "")),
                "fleet_rlm.llm_calls_remaining": str(max(0, self.max_llm_calls - self._llm_call_count)),
                "fleet_rlm.auth_preflight_result": "ok",
                "fleet_rlm.auth_fail_fast_state": str(getattr(self, "_sub_lm_auth_failed", False)).lower(),
            },
            inputs={"prompt_count": len(prompts), "prompt_chars": [len(prompt) for prompt in prompts]},
        ) as span:
            future_to_idx: dict[Any, int] = {}
            next_idx = 0
            auth_failure_seen = False

            def _submit_until_window() -> None:
                nonlocal next_idx, submitted_count
                while next_idx < len(prompts) and len(future_to_idx) < _LLM_QUERY_BATCH_WINDOW:
                    prompt = prompts[next_idx]
                    future = _LLM_BATCH_EXECUTOR.submit(
                        contextvars.copy_context().run,
                        self._query_sub_lm,
                        prompt,
                        target_lm=target_lm,
                        target_source=target_source,
                        target_model=target_model,
                    )
                    future_to_idx[future] = next_idx
                    submitted_count += 1
                    next_idx += 1

            _submit_until_window()
            while future_to_idx:
                future = next(as_completed(tuple(future_to_idx)))
                idx = int(future_to_idx.pop(future))
                try:
                    value = future.result()
                    results[idx] = value if isinstance(value, str) else str(value)
                except Exception as exc:
                    errors.append((idx, exc))
                    if self._is_auth_failure(exc) or getattr(self, "_sub_lm_auth_failed", False):
                        auth_failure_seen = True
                        canceled_pending = sum(1 for pending in tuple(future_to_idx) if pending.cancel())
                        future_to_idx.clear()
                        skipped_due_to_auth = max(0, len(prompts) - submitted_count) + canceled_pending
                        self._decrement_llm_calls(skipped_due_to_auth)
                        submitted_count -= canceled_pending
                        break
                if not auth_failure_seen:
                    _submit_until_window()

            set_mlflow_span_outputs(
                span,
                {
                    "status": "error" if errors else "ok",
                    "response_count": len(results),
                    "error_count": len(errors),
                    "batch_prompt_count": len(prompts),
                    "submitted_count": submitted_count,
                    "skipped_calls_due_to_auth_fail_fast": skipped_due_to_auth,
                    "auth_failure": auth_failure_seen,
                },
            )
            if errors and span is not None:
                set_status = getattr(span, "set_status", None)
                if callable(set_status):
                    set_status("ERROR")

        if errors:
            errors.sort(key=lambda x: x[0])
            details = "; ".join(f"prompt[{idx}]: {type(exc).__name__}: {exc}" for idx, exc in errors)
            raise RuntimeError(
                f"llm_query_batched failed for {len(errors)}/{len(prompts)} prompts: {details}"
            ) from errors[0][1]

        return [results[i] for i in range(len(prompts))]

    # ------------------------------------------------------------------
    # True-RLM symbolic recursion: sub_rlm / sub_rlm_batched
    # ------------------------------------------------------------------
    # These primitives implement Algorithm 1 from arXiv 2512.24601v2.
    # Unlike llm_query (a raw LLM call), sub_rlm spawns a full child
    # dspy.RLM interpreter loop — the child gets its own REPL context
    # and can write code, call llm_query, and even call sub_rlm again
    # (up to max_recursion_depth).
    # ------------------------------------------------------------------

    # Attributes set by initialize_sub_rlm_state():
    _sub_rlm_depth: int
    _sub_rlm_max_depth: int

    def sub_rlm(self, prompt: str, context: str = "") -> str:
        """Recursively invoke a child RLM from inside sandbox REPL code.

        This is the key primitive for true symbolic recursion (Algorithm 1
        from the RLM paper).  Code running in the sandbox can call::

            result = sub_rlm("Classify this chunk: " + chunk)

        or loop over slices::

            results = []
            for chunk in chunks:
                results.append(sub_rlm(f"Summarize: {chunk}"))
            Final = "\\n".join(results)

        Each call spawns a child dspy.RLM with its own REPL, sharing the
        parent's LLM budget.

        Phase 7: When max recursion depth is reached, this falls back to
        a single LLM call (llm_query) instead of raising an error. This
        ensures graceful degradation when the recursion ceiling is hit.

        Args:
            prompt: Task for the child RLM to solve.
            context: Optional supporting context string.

        Returns:
            The child RLM's answer as a string (or llm_query fallback at max depth).

        Raises:
            RuntimeError: If LLM budget exhausted.
        """
        if not prompt:
            raise ValueError("sub_rlm prompt cannot be empty")
        if self._sub_rlm_depth >= self._sub_rlm_max_depth:
            # Phase 7: fallback to llm_query at max depth instead of raising
            logger.info(
                "sub_rlm max recursion depth (%s) reached; falling back to llm_query",
                self._sub_rlm_max_depth,
            )
            full_prompt = prompt
            if context:
                full_prompt = f"{context}\n\n{prompt}"
            return self.llm_query(full_prompt)
        return self._execute_sub_rlm(prompt, context)

    def sub_rlm_batched(self, prompts: list[str], context: str = "") -> list[str]:
        """Invoke child RLMs in parallel for a batch of prompts.

        Equivalent to ``[sub_rlm(p, context) for p in prompts]`` but runs
        concurrently using a thread pool.

        Phase 7: When max recursion depth is reached, this falls back to
        llm_query_batched instead of raising an error.

        Args:
            prompts: List of task prompts for child RLMs.
            context: Shared context string for all children.

        Returns:
            List of answer strings, one per prompt (same order).

        Raises:
            RuntimeError: On budget violations or child failures.
        """
        if not prompts:
            return []
        if self._sub_rlm_depth >= self._sub_rlm_max_depth:
            # Phase 7: fallback to llm_query_batched at max depth
            logger.info(
                "sub_rlm_batched max recursion depth (%s) reached; falling back to llm_query_batched",
                self._sub_rlm_max_depth,
            )
            full_prompts = [f"{context}\n\n{p}" if context else p for p in prompts]
            return self.llm_query_batched(full_prompts)

        leases = self._sub_rlm_budget_leases(len(prompts))
        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []

        future_to_idx = {
            _SUB_RLM_BATCH_EXECUTOR.submit(
                contextvars.copy_context().run,
                self._execute_sub_rlm,
                p,
                context,
                leases[i],
            ): i
            for i, p in enumerate(prompts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = str(future.result())
            except Exception as exc:
                errors.append((idx, exc))

        if errors:
            errors.sort(key=lambda x: x[0])
            details = "; ".join(f"prompt[{i}]: {type(e).__name__}: {e}" for i, e in errors)
            raise RuntimeError(f"sub_rlm_batched failed for {len(errors)}/{len(prompts)}: {details}") from errors[0][1]

        return [results[i] for i in range(len(prompts))]

    def _execute_sub_rlm(
        self,
        prompt: str,
        context: str = "",
        llm_budget_lease: int | None = None,
    ) -> str:
        """Spawn a child dspy.RLM interpreter and return its answer."""
        remaining = self._remaining_llm_budget()
        if remaining <= 0:
            raise RuntimeError("LLM call budget exhausted — cannot spawn sub_rlm child.")
        child_budget = remaining
        if llm_budget_lease is not None:
            child_budget = max(0, min(remaining, int(llm_budget_lease)))
        if child_budget <= 0:
            raise RuntimeError("LLM call budget exhausted — cannot spawn sub_rlm child.")

        child = self.build_delegate_child(remaining_llm_budget=child_budget)
        if llm_budget_lease is not None:
            self._install_child_budget_lease(child, child_budget)
        max_iterations = max(1, min(getattr(self, "rlm_max_iterations", 30), child_budget))

        # Phase 7: build bounded conversation snapshot for child context
        history_snapshot = _build_child_history_snapshot(self)
        full_context = context
        if history_snapshot:
            full_context = f"{history_snapshot}\n\n{context}" if context else history_snapshot

        child_module = build_recursive_subquery_rlm(
            interpreter=child,
            max_iterations=max_iterations,
            max_llm_calls=child_budget,
            verbose=False,
            sub_lm=self.sub_lm,
        )

        try:
            child.start()
            metadata = getattr(child, "child_isolation_metadata", None)
            session = getattr(child, "_session", None)
            sandbox_id = getattr(session, "sandbox_id", None)
            if isinstance(metadata, dict) and sandbox_id:
                metadata.setdefault("child_sandbox_id", sandbox_id)
            prediction = child_module(prompt=prompt, context=full_context or "")
            return _validated_child_answer(prediction)
        except Exception as exc:
            logger.warning("sub_rlm child failed: %s", exc, exc_info=True)
            raise RuntimeError(f"sub_rlm failed: {exc}") from exc
        finally:
            try:
                child.shutdown()
                metadata = getattr(child, "child_isolation_metadata", None)
                if isinstance(metadata, dict):
                    metadata["cleanup_status"] = "deleted"
                logger.info("sub_rlm child cleanup complete: %s", metadata)
            except Exception:
                metadata = getattr(child, "child_isolation_metadata", None)
                if isinstance(metadata, dict):
                    metadata["cleanup_status"] = "failed"
                logger.warning("sub_rlm child cleanup failed: %s", metadata)
                pass  # Child may already be stopped

    def _remaining_llm_budget(self) -> int:
        """Return how many LLM calls remain in the shared budget."""
        with self._llm_call_lock:
            return max(0, self.max_llm_calls - self._llm_call_count)

    def _sub_rlm_budget_leases(self, child_count: int) -> list[int]:
        remaining = self._remaining_llm_budget()
        if remaining < child_count:
            raise RuntimeError(
                f"LLM call budget exhausted — cannot spawn {child_count} sub_rlm "
                f"children with only {remaining} semantic call(s) remaining."
            )
        base, extra = divmod(remaining, child_count)
        return [base + (1 if idx < extra else 0) for idx in range(child_count)]

    def _install_child_budget_lease(self, child: Any, lease: int) -> None:
        parent_check = self._check_and_increment_llm_calls
        parent_remaining = self._remaining_llm_budget
        lock = threading.Lock()
        consumed = 0
        lease = max(0, int(lease))

        def _check_and_increment(n: int = 1) -> None:
            nonlocal consumed
            n_int = int(n)
            if n_int < 0:
                raise ValueError("LLM call increment cannot be negative")
            with lock:
                if consumed + n_int > lease:
                    raise RuntimeError(f"LLM budget lease exceeded: {consumed} + {n_int} > {lease}.")
                parent_check(n_int)
                consumed += n_int

        def _remaining() -> int:
            with lock:
                local_remaining = max(0, lease - consumed)
            return min(local_remaining, parent_remaining())

        child._check_and_increment_llm_calls = _check_and_increment
        child._remaining_llm_budget = _remaining
        child.max_llm_calls = lease
        metadata = getattr(child, "child_isolation_metadata", None)
        if isinstance(metadata, dict):
            metadata["llm_budget_lease"] = lease


def _validated_child_answer(prediction: Any) -> str:
    if contains_marker(prediction, _BROKER_ERROR_MARKER):
        raise RuntimeError("Daytona broker unavailable during child RLM execution.")
    raw_answer = getattr(prediction, "answer", None)
    if raw_answer is None:
        raise RuntimeError("Child RLM completed without SUBMIT(answer=...).")
    return str(raw_answer)


def metadata_summary(
    text: str,
    *,
    preview_length: int = 200,
    label: str = "Output",
) -> str:
    """Produce a metadata-only summary of a large text result.

    Use this in tool return values when the full output is large and the
    caller should use REPL variables or sub_rlm() to process it rather
    than reading the whole string in the LLM context.

    Returns a short string like:
        ``[Output: 45,230 chars] First 200 chars: ...``

    This pattern is aligned with how ``dspy.RLM``'s ``REPLEntry.format()``
    shows ``Output (N chars):`` — keeping things concise forces the LLM
    to work through REPL state.
    """
    length = len(text)
    if length <= preview_length:
        return text
    preview = text[:preview_length].rstrip()
    return f"[{label}: {length:,} chars] First {preview_length} chars: {preview}..."
