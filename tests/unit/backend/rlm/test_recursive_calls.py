from __future__ import annotations

import time

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import ToolCompleted, ToolStarted
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor, RecursiveRLMOptions


def _executor(
    root_actions: list[dict[str, str]],
    *,
    sub_actions: list[dict[str, str]] | None = None,
    options: RecursiveRLMOptions | None = None,
    factory_calls: list[DaytonaCodeInterpreter] | None = None,
    observer=None,
) -> RecursiveRLMExecutor:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM(sub_actions or [{"answer": "fallback"}], adapter=adapter)

    def factory() -> DaytonaCodeInterpreter:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        if factory_calls is not None:
            factory_calls.append(interpreter)
        return interpreter

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_interpreter_factory=factory,
        deadline=time.monotonic() + 30,
        observer=observer,
    )


def test_recursive_tool_runs_fresh_native_child_and_redacts_observation() -> None:
    events = []
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
        factory_calls=created,
        observer=events.append,
    )

    result = executor.tool(prompt="classify selected row")

    assert result == "child-ok"
    assert len(created) == 1
    assert created[0]._shutdown
    assert executor.summary().call_count == 1
    assert executor.summary().child_iterations == 1
    started = next(event for event in events if isinstance(event, ToolStarted))
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert started.input == {"prompt_count": 1, "prompt_chars": len("classify selected row")}
    assert completed.output == {"status": "completed"}
    assert "classify selected row" not in repr(events)
    assert "child-ok" not in repr(events)


def test_recursive_tool_uses_sub_lm_at_depth_cap_without_new_interpreter() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [
            {"reasoning": "delegate deeper", "code": "inner = rlm_query('inner slice')"},
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner)"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
    )

    assert executor.tool(prompt="outer slice") == "fallback-answer"
    assert len(created) == 1
    assert executor.summary().call_count == 2
    assert executor.summary().depth_fallback_count == 1
    assert "depth_fallback" in executor.summary().termination_modes


@pytest.mark.parametrize(
    ("prompt", "message"),
    [("", "must not be empty"), ("x" * 11, "character bound")],
)
def test_recursive_tool_rejects_invalid_prompt_before_child_creation(prompt: str, message: str) -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_prompt_chars=10),
        factory_calls=created,
    )

    with pytest.raises(ValueError, match=message):
        executor.tool(prompt=prompt)
    assert created == []


def test_recursive_tool_enforces_shared_call_budget() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='ok')"}],
        options=RecursiveRLMOptions(max_calls=1),
    )

    assert executor.tool(prompt="first") == "ok"
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.tool(prompt="second")
