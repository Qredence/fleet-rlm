"""Shared helper functions for interpreter implementations."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable

import dspy

from .profiles import ExecutionProfile


def initialize_llm_query_state(
    target: Any,
    *,
    sub_lm: dspy.LM | None,
    max_llm_calls: int,
    llm_call_timeout: int,
) -> None:
    """Populate shared LLM-query state used by interpreter backends."""
    target.sub_lm = sub_lm
    target.max_llm_calls = max_llm_calls
    target.llm_call_timeout = llm_call_timeout
    target._llm_call_count = 0
    target._llm_call_lock = threading.Lock()
    target._sub_lm_executor = None
    target._sub_lm_executor_lock = threading.Lock()


def initialize_tool_runtime_state(target: Any) -> None:
    """Populate shared tool and execution callback state."""
    target.output_fields = None
    target._tools = {}
    target.execution_event_callback = None


def get_registered_tools(target: Any) -> dict[str, Callable[..., Any]]:
    """Return the registered tool map for an interpreter."""
    return target._tools


def set_registered_tools(
    target: Any,
    value: dict[str, Callable[..., Any]],
) -> None:
    """Replace the registered tool map for an interpreter."""
    target._tools = value


@contextmanager
def execution_profile_context(
    target: Any,
    profile: ExecutionProfile,
):
    """Temporarily override the default execution profile."""
    previous = target.default_execution_profile
    target.default_execution_profile = profile
    try:
        yield target
    finally:
        target.default_execution_profile = previous


def sync_enter(target: Any) -> Any:
    """Start an interpreter for sync context manager usage."""
    target.start()
    return target


def sync_exit(target: Any) -> bool:
    """Shutdown an interpreter for sync context manager usage."""
    target.shutdown()
    return False


async def async_enter(target: Any) -> Any:
    """Start an interpreter for async context manager usage."""
    if target.async_execute:
        await target.astart()
    else:
        target.start()
    return target


async def async_exit(target: Any) -> bool:
    """Shutdown an interpreter for async context manager usage."""
    if target.async_execute:
        await target.ashutdown()
    else:
        target.shutdown()
    return False
