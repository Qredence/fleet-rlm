"""LLM query tools for ModalInterpreter.

This module provides the LLMQueryMixin class, extracted from ModalInterpreter
for better maintainability and separation of concerns.

The mixin provides built-in RLM tools for recursive LLM calls:
    - llm_query: Query a sub-LLM for semantic analysis
    - llm_query_batched: Query multiple prompts concurrently

Classes:
    - LLMQueryMixin: Mixin providing LLM query capabilities
"""

from __future__ import annotations

import contextvars
import threading
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)

import dspy


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
