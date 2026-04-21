"""Simplified delegate_to_rlm tool for RLM delegation in a Daytona sandbox.

Registers a single module-level ``delegate_to_rlm`` function marked with
``@tool_fn`` so that ``discover_tools()`` can collect it.

The tool uses a ``contextvars.ContextVar`` to hold the active Daytona
interpreter for the current agent turn.  Call ``set_delegate_interpreter()``
before invoking the tool to inject the interpreter.  Calling without a set
interpreter raises ``RuntimeError``.

Usage within an agent runtime::

    from fleet_rlm.runtime.tools.rlm_delegate import set_delegate_interpreter
    token = set_delegate_interpreter(interpreter)
    try:
        result = delegate_to_rlm("my query", "optional context")
    finally:
        _delegate_interpreter.reset(token)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm
from fleet_rlm.runtime.tools._marker import tool_fn

logger = logging.getLogger(__name__)

# Context variable holding the Daytona interpreter for the active agent turn.
# Set by the agent runtime before invoking the agent so tool calls can
# access the interpreter without requiring a closure.
_delegate_interpreter: ContextVar[Any | None] = ContextVar(
    "rlm_delegate_interpreter", default=None
)


def set_delegate_interpreter(interpreter: Any | None) -> Any:
    """Set the active Daytona interpreter for RLM delegation.

    The returned token can be passed to ``_delegate_interpreter.reset(token)``
    to restore the previous value (useful in tests and nested contexts).

    Args:
        interpreter: Daytona interpreter instance, or ``None`` to clear.

    Returns:
        A ``contextvars.Token`` for resetting the variable.
    """
    return _delegate_interpreter.set(interpreter)


@tool_fn
def delegate_to_rlm(query: str, context: str = "") -> dict[str, Any]:
    """Delegate a query to a recursive dspy.RLM running in a Daytona sandbox.

    Creates or reuses an existing Daytona sandbox session, constructs a
    ``dspy.RLM`` with the active interpreter, executes the query, and returns
    a structured result dict with ``status`` and ``answer``.

    The interpreter must be set via :func:`set_delegate_interpreter` before
    invoking this tool (or via the surrounding agent runtime context that
    initialises the Daytona interpreter).

    Args:
        query: The query to execute in the recursive RLM.
        context: Optional additional context string for the query.

    Returns:
        A dict with:
        - ``status``: ``"ok"`` on success, ``"error"`` on failure.
        - ``answer``: The RLM result string (present when ``status == "ok"``).
        - ``error``: Error message string (present when ``status == "error"``).

    Raises:
        RuntimeError: When called without a bound interpreter in the
            current context (i.e., ``set_delegate_interpreter`` was not called).
    """
    interpreter = _delegate_interpreter.get()
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm requires a bound Daytona interpreter. "
            "Set the interpreter via set_delegate_interpreter() or run within "
            "an agent runtime context that initialises the interpreter."
        )

    # Ensure the sandbox session is started (creates or reuses the session).
    if not getattr(interpreter, "_started", False):
        interpreter.start()

    rlm = build_recursive_subquery_rlm(
        interpreter=interpreter,
        max_iterations=20,
        max_llm_calls=50,
        verbose=bool(getattr(interpreter, "verbose", False)),
    )

    try:
        prediction = rlm(prompt=query, context=context)
        answer = str(getattr(prediction, "answer", "") or "")
        return {"status": "ok", "answer": answer}
    except Exception as exc:
        logger.warning("delegate_to_rlm execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}


__all__ = ["delegate_to_rlm", "set_delegate_interpreter"]
