from __future__ import annotations

import time
from collections.abc import Callable

import dspy
import pytest

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import Status, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor, RecursiveRLMOptions


def _executor(
    root_actions: list[dict[str, str]],
    *,
    sub_actions: list[dict[str, str]] | None = None,
    options: RecursiveRLMOptions | None = None,
    factory_calls: list[DaytonaCodeInterpreter] | None = None,
    observer=None,
    is_authorized: Callable[[], bool] | None = None,
) -> RecursiveRLMExecutor:
    """
    Construct a recursive executor backed by dummy root and sub-models for tests.
    
    Parameters:
        root_actions (list[dict[str, str]]): Responses supplied by the root model.
        sub_actions (list[dict[str, str]] | None): Responses supplied by the sub-model.
        options (RecursiveRLMOptions | None): Executor configuration.
        factory_calls (list[DaytonaCodeInterpreter] | None): Collection to receive created child interpreters.
        observer: Optional event observer.
        is_authorized (Callable[[], bool] | None): Optional callback used to authorize recursive calls.
    
    Returns:
        RecursiveRLMExecutor: A configured executor with an in-process child-runtime factory.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM(sub_actions or [{"answer": "fallback"}], adapter=adapter)

    def factory(call_index: int) -> ChildRuntimeLease:
        """Create a child runtime lease backed by an in-process interpreter.
        
        Parameters:
        	call_index (int): Index used to identify the child runtime.
        
        Returns:
        	ChildRuntimeLease: A lease for the newly created child runtime.
        """
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        if factory_calls is not None:
            factory_calls.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
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
    assert completed.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event, Status)]
    assert [event.status for event in statuses] == ["child_started", "child_completed"]
    assert statuses[0].message == "call_index=1 recursive_depth=1"
    assert statuses[1].message is not None
    assert "duration_ms=" in statuses[1].message
    assert "cleanup_status=completed" in statuses[1].message
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


def test_recursive_tool_rejects_revoked_authority_before_child_creation() -> None:
    authority = RunAuthority()
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=lambda: not authority.revoked,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="late child request")

    assert created == []
    assert len(events) == 1
    assert isinstance(events[0], ToolFailed)


def test_recursive_tool_rechecks_authority_before_child_allocation() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether an authorization check is permitted.
        
        Returns:
        	bool: `true` for the first check, `false` for subsequent checks.
        """
        nonlocal checks
        checks += 1
        return checks == 1

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked before allocation")

    assert created == []
    assert [event.status for event in events if isinstance(event, Status)] == []


def test_recursive_tool_closes_lease_when_authority_is_revoked_after_acquisition() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determine whether authorization remains available for the current check.
        
        Returns:
            bool: `True` for the first four checks, and `False` thereafter.
        """
        nonlocal checks
        checks += 1
        return checks < 5

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked after acquisition")

    assert len(created) == 1
    assert created[0]._shutdown
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message
    assert "cleanup_status=completed" in failed[0].message


def test_recursive_tool_discards_result_when_authority_is_revoked_after_execution() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether authorization remains valid for the next check.
        
        Returns:
        	bool: `True` for the first six checks and `False` thereafter.
        """
        nonlocal checks
        checks += 1
        return checks < 7

    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-secret')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked after execution")

    assert len(created) == 1
    assert created[0]._shutdown
    assert "child-secret" not in repr(events)
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message
