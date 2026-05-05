"""LLM query mixin and helpers for interpreter-backed RLM flows.

Provides :class:`LLMQueryMixin` with built-in RLM tools for recursive LLM calls
(``llm_query``, ``llm_query_batched``) and true-RLM symbolic recursion primitives
(``sub_rlm``, ``sub_rlm_batched``).
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from typing import Any
from unittest.mock import Mock

import dspy

from fleet_rlm.runtime.modules.factory import build_recursive_subquery_rlm

logger = logging.getLogger(__name__)

_BROKER_ERROR_MARKER = "Broker server failed to start"


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
                "No LM configured. Use dspy.configure(lm=...) or pass sub_lm to the active interpreter."
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

        # Reuse an executor with modest concurrency to avoid creating unbounded
        # threads when repeated calls time out, while not serializing all calls.
        with self._sub_lm_executor_lock:
            if self._sub_lm_executor is None:
                self._sub_lm_executor = ThreadPoolExecutor(
                    max_workers=min(8, max(1, self.max_llm_calls))
                )
            executor = self._sub_lm_executor

        ctx = contextvars.copy_context()
        future = executor.submit(ctx.run, _execute_lm)
        try:
            result = future.result(timeout=self.llm_call_timeout)
            return result if isinstance(result, str) else str(result)
        except FutureTimeoutError as exc:
            future.cancel()
            # Running threads cannot be cancelled; discard the exhausted executor
            # so subsequent calls get a fresh worker pool.
            with self._sub_lm_executor_lock:
                if self._sub_lm_executor is not None:
                    self._sub_lm_executor.shutdown(wait=False)
                    self._sub_lm_executor = None
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

        leases = self._sub_rlm_budget_leases(len(prompts))
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
            details = "; ".join(
                f"prompt[{i}]: {type(e).__name__}: {e}" for i, e in errors
            )
            raise RuntimeError(
                f"sub_rlm_batched failed for {len(errors)}/{len(prompts)}: {details}"
            ) from errors[0][1]

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
            raise RuntimeError(
                "LLM call budget exhausted — cannot spawn sub_rlm child."
            )
        child_budget = remaining
        if llm_budget_lease is not None:
            child_budget = max(0, min(remaining, int(llm_budget_lease)))
        if child_budget <= 0:
            raise RuntimeError(
                "LLM call budget exhausted — cannot spawn sub_rlm child."
            )

        child = self.build_delegate_child(remaining_llm_budget=child_budget)
        if llm_budget_lease is not None:
            self._install_child_budget_lease(child, child_budget)
        max_iterations = max(
            1, min(getattr(self, "rlm_max_iterations", 30), child_budget)
        )

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
            prediction = child_module(prompt=prompt, context=context or "")
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
                    raise RuntimeError(
                        f"LLM budget lease exceeded: {consumed} + {n_int} > {lease}."
                    )
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
    if _contains_marker(prediction, _BROKER_ERROR_MARKER):
        raise RuntimeError("Daytona broker unavailable during child RLM execution.")
    raw_answer = getattr(prediction, "answer", None)
    if raw_answer is None:
        raise RuntimeError("Child RLM completed without SUBMIT(answer=...).")
    return str(raw_answer)


def _contains_marker(value: Any, marker: str, *, _depth: int = 0) -> bool:
    if _depth > 6:
        return False
    if isinstance(value, str):
        return marker in value
    if value is None or isinstance(value, (bool, int, float)):
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_marker(item, marker, _depth=_depth + 1)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_marker(item, marker, _depth=_depth + 1) for item in value)
    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        filtered = {
            key: item
            for key, item in value_dict.items()
            if key
            in {"answer", "reasoning", "code", "trajectory", "repl_history", "history"}
        }
        if _contains_marker(filtered, marker, _depth=_depth + 1):
            return True
    if not isinstance(value, Mock):
        for attr in (
            "answer",
            "reasoning",
            "code",
            "trajectory",
            "repl_history",
            "history",
        ):
            try:
                attr_value = getattr(value, attr)
            except Exception:
                continue
            if _contains_marker(attr_value, marker, _depth=_depth + 1):
                return True
    return False


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
