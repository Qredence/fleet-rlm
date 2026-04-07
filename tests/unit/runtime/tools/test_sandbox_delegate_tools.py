from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

# Prime the known llm_tools/delegation_policy circular-import path once so this
# direct-module regression test can import sandbox_delegate_tools afterward.
try:
    import fleet_rlm.runtime.tools.llm_tools  # noqa: F401
except ImportError:
    # Optional priming import for regression setup; safe to continue when unavailable.
    pass

from fleet_rlm.runtime.tools import sandbox_delegate_tools


def _build_registration(spec: sandbox_delegate_tools._CachedRuntimeToolSpec):
    """Build a single generated tool registration against a minimal fake agent."""
    agent = SimpleNamespace(
        interpreter=SimpleNamespace(mark_runtime_degradation=None),
        _current_depth=0,
    )
    ctx = sandbox_delegate_tools._DelegateToolContext(agent=agent)
    return sandbox_delegate_tools._build_cached_runtime_tool(ctx, spec)


def test_plan_code_change_uses_falsy_fallback_for_default_constraints(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_run_runtime_module(_agent, module_name: str, **kwargs: Any):
        captured["module_name"] = module_name
        captured["kwargs"] = kwargs
        return (SimpleNamespace(depth=1, sub_agent_history=0), None, False)

    monkeypatch.setattr(
        sandbox_delegate_tools,
        "_run_runtime_module",
        _fake_run_runtime_module,
    )

    registration = _build_registration(sandbox_delegate_tools._PLAN_CODE_CHANGE)
    tool_fn = sandbox_delegate_tools._sync_compatible_tool_callable(registration.func)

    result = tool_fn("update runtime")

    assert result["status"] == "ok"
    assert captured["module_name"] == "plan_code_change"
    assert captured["kwargs"]["constraints"] == "Keep changes minimal."


@pytest.mark.parametrize(
    ("call_args", "call_kwargs", "message"),
    [
        ((), {}, "missing 1 required positional argument"),
        (
            ("focus", "active", True, "extra"),
            {},
            "takes from 1 to 3 positional arguments",
        ),
        (("focus",), {"focus": "override"}, "multiple values for argument 'focus'"),
        (
            ("focus",),
            {"unexpected": "value"},
            "got an unexpected keyword argument 'unexpected'",
        ),
    ],
)
def test_generated_tool_callable_preserves_python_argument_validation(
    call_args: tuple[Any, ...], call_kwargs: dict[str, Any], message: str
):
    tool_fn = _build_registration(sandbox_delegate_tools._SUMMARIZE_LONG_DOCUMENT).func

    with pytest.raises(TypeError, match=message):
        tool_fn(*call_args, **call_kwargs)


def test_generated_tool_callable_exposes_declared_signature():
    tool_fn = _build_registration(sandbox_delegate_tools._PLAN_CODE_CHANGE).func

    assert str(inspect.signature(tool_fn)) == (
        "(task, repo_context='', constraints='', include_trajectory=True)"
    )


@pytest.mark.parametrize(
    ("tool_name", "param_order"),
    [
        ("not-valid-name", ("query",)),
        ("def", ("query",)),
        ("valid_name", ("1query",)),
    ],
)
def test_generated_tool_callable_rejects_invalid_identifiers(
    tool_name: str, param_order: tuple[str, ...]
):
    spec = sandbox_delegate_tools._CachedRuntimeToolSpec(
        name=tool_name,
        desc="desc",
        module_name="module",
        param_order=param_order,
    )

    with pytest.raises(ValueError, match="Invalid generated tool signature"):
        _build_registration(spec)
