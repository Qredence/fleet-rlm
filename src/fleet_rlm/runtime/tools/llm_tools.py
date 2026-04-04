"""LLM query tools and runtime module helpers for ModalInterpreter.

This module provides:
  - LLMQueryMixin: Mixin providing built-in RLM tools for recursive LLM calls
    (llm_query, llm_query_batched) and true-RLM symbolic recursion primitives
    (sub_rlm, sub_rlm_batched).
  - Shared helpers for tools that call cached runtime modules via
    ``agent.get_runtime_module(...)``.  These cover the non-recursive path;
    explicit recursion still flows through ``rlm_query`` and
    :mod:`fleet_rlm.runtime.agent.recursive_runtime`.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from typing import TYPE_CHECKING, Any

import dspy

from fleet_rlm.runtime.agent.delegation_policy import (
    RuntimeModuleExecutionRequest,
    invoke_runtime_module,
)

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)


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

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
        """Create a child interpreter — implemented by the host class."""

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

    def _query_sub_lm(self, prompt: str) -> str:
        """Query the sub-LM with a prompt string.

        Args:
            prompt: The prompt to send to the sub-LM.

        Returns:
            The response text from the sub-LM.

        Raises:
            RuntimeError: If no LM is configured or if the call times out.
        """
        target_lm = self.sub_lm if self.sub_lm is not None else dspy.settings.lm
        if target_lm is None:
            raise RuntimeError(
                "No LM configured. Use dspy.configure(lm=...) or pass sub_lm to ModalInterpreter."
            )

        # Execute LM call with timeout to prevent hangs
        def _execute_lm() -> str:
            response = target_lm(prompt)
            if isinstance(response, list) and response:
                item = response[0]
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
                return str(item)
            return str(response)

        # Reuse a single-worker executor to avoid creating unbounded background
        # threads when repeated calls time out.
        with self._sub_lm_executor_lock:
            if self._sub_lm_executor is None:
                self._sub_lm_executor = ThreadPoolExecutor(max_workers=1)
            executor = self._sub_lm_executor

        ctx = contextvars.copy_context()
        future = executor.submit(ctx.run, _execute_lm)
        try:
            result = future.result(timeout=self.llm_call_timeout)
            return result if isinstance(result, str) else str(result)
        except FutureTimeoutError as exc:
            future.cancel()
            raise RuntimeError(
                f"LLM call timed out after {self.llm_call_timeout}s. "
                "Consider increasing llm_call_timeout or checking API connectivity."
            ) from exc

    def llm_query(self, prompt: str) -> str:
        """Query a sub-LLM for semantic analysis.

        This is a built-in RLM tool that allows sandboxed code to make
        recursive LLM calls. Each call counts against max_llm_calls.

        Args:
            prompt: The prompt to send to the sub-LLM.

        Returns:
            The response text from the sub-LLM.

        Raises:
            ValueError: If prompt is empty.
            RuntimeError: If max_llm_calls would be exceeded.

        Example:
            >>> result = llm_query("Summarize this text in one sentence.")
        """
        if not prompt:
            raise ValueError("prompt cannot be empty")
        self._check_and_increment_llm_calls(1)
        return self._query_sub_lm(prompt)

    def llm_query_batched(self, prompts: list[str]) -> list[str]:
        """Query the sub-LLM with multiple prompts concurrently.

        This is a built-in RLM tool for making multiple LLM calls in parallel.
        Each prompt counts against max_llm_calls.

        Args:
            prompts: List of prompts to send to the sub-LLM.

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
        self._check_and_increment_llm_calls(len(prompts))

        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []

        # Adaptive ThreadPool sizing: use min of max_llm_calls and 8, or batch size
        # This prevents over-allocation for small batches and under-utilization for large ones
        adaptive_workers = max(1, min(len(prompts), self.max_llm_calls, 8))

        with ThreadPoolExecutor(max_workers=adaptive_workers) as executor:
            future_to_idx = {
                # Copy a fresh context per task. Reusing one Context object
                # across concurrent threads can raise:
                # "RuntimeError: cannot enter context ... is already entered".
                executor.submit(
                    contextvars.copy_context().run, self._query_sub_lm, p
                ): i
                for i, p in enumerate(prompts)
            }
            for future in as_completed(future_to_idx):
                idx = int(future_to_idx[future])
                try:
                    value = future.result()
                    results[idx] = value if isinstance(value, str) else str(value)
                except Exception as exc:
                    errors.append((idx, exc))

        if errors:
            errors.sort(key=lambda x: x[0])
            details = "; ".join(
                f"prompt[{idx}]: {type(exc).__name__}: {exc}" for idx, exc in errors
            )
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

        Args:
            prompt: Task for the child RLM to solve.
            context: Optional supporting context string.

        Returns:
            The child RLM's answer as a string.

        Raises:
            RuntimeError: If recursion depth exceeded or LLM budget exhausted.
        """
        if not prompt:
            raise ValueError("sub_rlm prompt cannot be empty")
        if self._sub_rlm_depth >= self._sub_rlm_max_depth:
            raise RuntimeError(
                f"sub_rlm max recursion depth ({self._sub_rlm_max_depth}) reached. "
                "Cannot recurse further."
            )
        return self._execute_sub_rlm(prompt, context)

    def sub_rlm_batched(self, prompts: list[str], context: str = "") -> list[str]:
        """Invoke child RLMs in parallel for a batch of prompts.

        Equivalent to ``[sub_rlm(p, context) for p in prompts]`` but runs
        concurrently using a thread pool.

        Args:
            prompts: List of task prompts for child RLMs.
            context: Shared context string for all children.

        Returns:
            List of answer strings, one per prompt (same order).

        Raises:
            RuntimeError: On depth/budget violations or child failures.
        """
        if not prompts:
            return []
        if self._sub_rlm_depth >= self._sub_rlm_max_depth:
            raise RuntimeError(
                f"sub_rlm max recursion depth ({self._sub_rlm_max_depth}) reached."
            )

        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []
        workers = max(1, min(len(prompts), 4))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(
                    contextvars.copy_context().run,
                    self._execute_sub_rlm,
                    p,
                    context,
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
            details = "; ".join(
                f"prompt[{i}]: {type(e).__name__}: {e}" for i, e in errors
            )
            raise RuntimeError(
                f"sub_rlm_batched failed for {len(errors)}/{len(prompts)}: {details}"
            ) from errors[0][1]

        return [results[i] for i in range(len(prompts))]

    def _execute_sub_rlm(self, prompt: str, context: str = "") -> str:
        """Spawn a child dspy.RLM interpreter and return its answer."""
        # Late imports to avoid circular dependencies
        from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm

        remaining = self._remaining_llm_budget()
        if remaining <= 0:
            raise RuntimeError(
                "LLM call budget exhausted — cannot spawn sub_rlm child."
            )

        child = self.build_delegate_child(remaining_llm_budget=remaining)

        child_module = build_recursive_subquery_rlm(
            interpreter=child,
            max_iterations=min(30, remaining),
            max_llm_calls=remaining,
            verbose=False,
            sub_lm=self.sub_lm,
        )

        try:
            child.start()
            prediction = child_module(prompt=prompt, context=context or "")
            answer = str(getattr(prediction, "answer", "") or "")
            return answer
        except Exception as exc:
            logger.warning("sub_rlm child failed: %s", exc, exc_info=True)
            raise RuntimeError(f"sub_rlm failed: {exc}") from exc
        finally:
            try:
                child.shutdown()
            except Exception:
                pass

    def _remaining_llm_budget(self) -> int:
        """Return how many LLM calls remain in the shared budget."""
        with self._llm_call_lock:
            return max(0, self.max_llm_calls - self._llm_call_count)


# ---------------------------------------------------------------------------
# Cached runtime-module helpers (merged from runtime_module_helpers.py)
# ---------------------------------------------------------------------------


def _runtime_degradation_payload(agent: RLMReActChatAgent) -> dict[str, Any]:
    """Load runtime degradation metadata without a fragile module-level import."""
    from fleet_rlm.runtime.agent.chat_turns import runtime_degradation_payload

    return runtime_degradation_payload(agent)


def coerce_int(
    value: Any,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse *value* as an integer with optional bounds."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def coerce_str_list(value: Any) -> list[str]:
    """Normalize list-like prediction fields into a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def prediction_value(prediction: Any, field_name: str, default: Any) -> Any:
    """Read a field from either a dict-shaped or attribute-shaped prediction."""
    if isinstance(prediction, dict):
        return prediction.get(field_name, default)
    return getattr(prediction, field_name, default)


def run_cached_runtime_module(
    agent: RLMReActChatAgent,
    module_name: str,
    **kwargs: Any,
) -> tuple[Any | None, dict[str, Any] | None, bool]:
    """Invoke one cached runtime module through the shared delegation policy."""
    result = invoke_runtime_module(
        RuntimeModuleExecutionRequest(
            agent=agent,
            module_name=module_name,
            module_kwargs=kwargs,
        )
    )
    return result.prediction, result.error, result.fallback_used


def runtime_metadata(
    agent: RLMReActChatAgent,
    prediction: Any,
    *,
    fallback_used: bool,
) -> dict[str, Any]:
    """Return stable metadata shared by cached runtime-module tool results."""
    metadata: dict[str, Any] = {
        "depth": coerce_int(
            prediction_value(prediction, "depth", agent._current_depth + 1),
            default=agent._current_depth + 1,
            minimum=0,
        ),
        "sub_agent_history": coerce_int(
            prediction_value(prediction, "sub_agent_history", 0),
            default=0,
            minimum=0,
        ),
        "delegate_lm_fallback": bool(fallback_used),
        "runtime_degraded": False,
        "runtime_fallback_used": False,
    }
    metadata.update(_runtime_degradation_payload(agent))
    return metadata


# ---------------------------------------------------------------------------
# Output metadata helper (Algorithm 1 variable-mode support)
# ---------------------------------------------------------------------------


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
