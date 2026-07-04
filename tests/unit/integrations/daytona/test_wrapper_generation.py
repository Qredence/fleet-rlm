"""Phase 3 tool wrapper generation tests.

Validates ``generate_tool_wrapper`` default round-trip behaviour, positional-
only separators, and ``*args``/``**kwargs`` pass-through by exec-ing the
returned wrapper string in a fresh namespace.
"""

from __future__ import annotations

import inspect
from typing import Any

from fleet_rlm.integrations.daytona.bridge import generate_tool_wrapper


def _exec_wrapper(code: str) -> dict[str, Any]:
    """Exec the wrapper code and return the namespace."""
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102 - intentional exec for test harness
    return namespace


# ---------------------------------------------------------------------------
# Test 1: default={"nested": [1, 2]} round-trips → keyword param keeps default
# ---------------------------------------------------------------------------


def test_dict_default_round_trips() -> None:
    def foo(a, b={"nested": [1, 2]}):  # noqa: B006 - intentional mutable default for test
        pass

    code = generate_tool_wrapper(
        tool_name="foo",
        tool_func=foo,
        broker_secret="secret",
    )
    ns = _exec_wrapper(code)
    sig = inspect.signature(ns["foo"])
    assert sig.parameters["b"].default == {"nested": [1, 2]}
    assert sig.parameters["b"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD


# ---------------------------------------------------------------------------
# Test 2: default=object() (fails ast.literal_eval(repr(...))) → keyword-only
# ---------------------------------------------------------------------------


def test_non_repr_default_becomes_keyword_only() -> None:
    sentinel = object()

    def foo(a, b=sentinel):
        pass

    code = generate_tool_wrapper(
        tool_name="foo",
        tool_func=foo,
        broker_secret="secret",
    )
    ns = _exec_wrapper(code)
    sig = inspect.signature(ns["foo"])
    assert sig.parameters["b"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["b"].default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Test 3: Positional-only params keep the / separator
# ---------------------------------------------------------------------------


def test_positional_only_keeps_separator() -> None:
    def foo(a, /, b):
        pass

    code = generate_tool_wrapper(
        tool_name="foo",
        tool_func=foo,
        broker_secret="secret",
    )
    ns = _exec_wrapper(code)
    sig = inspect.signature(ns["foo"])
    assert sig.parameters["a"].kind == inspect.Parameter.POSITIONAL_ONLY
    assert sig.parameters["b"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD


# ---------------------------------------------------------------------------
# Test 4: *args and **kwargs pass through unchanged
# ---------------------------------------------------------------------------


def test_args_kwargs_pass_through() -> None:
    def foo(*args, **kwargs):
        pass

    code = generate_tool_wrapper(
        tool_name="foo",
        tool_func=foo,
        broker_secret="secret",
    )
    ns = _exec_wrapper(code)
    sig = inspect.signature(ns["foo"])
    assert sig.parameters["args"].kind == inspect.Parameter.VAR_POSITIONAL
    assert sig.parameters["kwargs"].kind == inspect.Parameter.VAR_KEYWORD
