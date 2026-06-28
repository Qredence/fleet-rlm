"""Unit tests for the RLM kwargs reconstruction and iteration extraction fix.

Covers validation-contract assertions:
- VAL-SEC-014: ``_execute_iteration`` forwards ``variables_info``,
  ``repl_history``, and ``iteration`` to ``generate_action`` (no ``TypeError``).
- VAL-SEC-015: ``_execute_iteration`` does not raise ``TypeError`` on a normal
  RLM iteration.
- VAL-SEC-016: iteration number is extracted from ``args[3]`` positional arg,
  not from the (empty) ``kwargs`` or a nonexistent attribute.
- VAL-SEC-017: progress events report the correct, monotonically increasing
  iteration number across multiple iterations.
- VAL-SEC-018: ``_is_parse_error`` narrowed to JSON-specific markers /
  ``json.JSONDecodeError`` (broad substrings like ``"invalid"`` do not match).

Background: the base ``dspy.RLM.forward`` calls
``_execute_iteration(repl, variables, history, iteration, input_args,
output_field_names)`` positionally, so the override's ``**kwargs`` is empty.
The override must reconstruct the kwargs (``variables_info``,
``repl_history``, ``iteration``) from the positional args before calling
``_run_action(**kwargs)``, and must read ``iteration`` from ``args[3]`` so
progress events and MLflow spans report the correct iteration number.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.integrations.observability import mlflow_context
from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
from fleet_rlm.runtime.modules.factory import _StreamingRLM, create_runtime_rlm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSpan:
    """Minimal MLflow span stub capturing attributes and outputs."""

    def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
        self.record: dict[str, Any] = {
            "name": name,
            "span_type": span_type,
            "attributes": dict(attributes or {}),
            "outputs": None,
            "status": "OK",
        }

    def __enter__(self) -> "_FakeSpan":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def set_inputs(self, inputs: Any) -> None:
        self.record["inputs"] = inputs

    def set_outputs(self, outputs: Any) -> None:
        self.record["outputs"] = outputs

    def set_status(self, status: str) -> None:
        self.record["status"] = status


def _patch_mlflow(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch ``mlflow_child_span`` with a capturing FakeSpan; return capture list."""
    captured: list[dict[str, Any]] = []

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        start_span=lambda name, span_type=None, attributes=None: _FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )
    # Also patch the convenience wrappers used by _execute_iteration.
    monkeypatch.setattr(
        mlflow_context,
        "mlflow_child_span",
        lambda name, span_type=None, attributes=None, inputs=None: _FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "set_mlflow_span_outputs",
        lambda span, outputs: span.record.__setitem__("outputs", outputs) if span is not None else None,
    )
    return captured


def _make_rlm(*, max_iterations: int = 3) -> tuple[Any, list[dict[str, Any]]]:
    """Build a real ``_StreamingRLM`` with a mocked inner action predictor.

    Returns ``(rlm, events)`` where ``events`` collects every ``_emit_step``
    payload via the interpreter's ``_turn_step_callback``.
    """
    events: list[dict[str, Any]] = []
    interpreter = SimpleNamespace(_turn_step_callback=events.append)
    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=10,
        verbose=False,
    )
    assert isinstance(rlm, _StreamingRLM)
    return rlm, events


class _FakeVariable:
    """Stand-in for a ``REPLVariable`` with a ``format()`` method."""

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value

    def format(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} = {self.value}"


# ---------------------------------------------------------------------------
# VAL-SEC-014 / VAL-SEC-015: kwargs reconstruction → no TypeError
# ---------------------------------------------------------------------------


def test_execute_iteration_reconstructs_kwargs_and_does_not_raise_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_execute_iteration`` reconstructs kwargs from positional args so
    ``generate_action`` receives ``variables_info``, ``repl_history``, and
    ``iteration`` — and the call completes without ``TypeError``.

    Reproduces the bug: the base class calls ``_execute_iteration`` with all
    args positional, so ``kwargs`` is empty. Without reconstruction,
    ``_run_action(**{})`` calls ``generate_action()`` with no kwargs, which
    raises ``TypeError: ... missing required argument``.
    """
    _patch_mlflow(monkeypatch)
    rlm, _events = _make_rlm(max_iterations=3)

    captured_kwargs: dict[str, Any] = {}

    class FakeInner:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            captured_kwargs.update(kwargs)
            return dspy.Prediction(reasoning="ok", code="SUBMIT(response='done')")

    rlm.generate_action._inner = FakeInner()
    # Force the bounded-LM path off so _run_action uses the plain global LM path.
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)

    # _execute_iteration now EXECUTES the generated code (regression fix), so
    # the repl must support ``.execute()``; returning a ``FinalOutput`` simulates
    # a successful SUBMIT → a ``Prediction`` with a trajectory.
    from dspy.primitives.code_interpreter import FinalOutput
    from dspy.primitives.repl_types import REPLHistory

    repl = _FakeRepl(result=FinalOutput({"response": "done"}))
    variables = [_FakeVariable("user_request", "hello")]
    history = REPLHistory(max_output_chars=1500)

    # Base dspy.RLM.forward signature: (repl, variables, history, iteration, input_args, output_field_names)
    result = rlm._execute_iteration(repl, variables, history, 0, {}, ["response"])

    # generate_action must have received all three required kwargs.
    assert "variables_info" in captured_kwargs, "generate_action did not receive variables_info"
    assert "repl_history" in captured_kwargs, "generate_action did not receive repl_history"
    assert "iteration" in captured_kwargs, "generate_action did not receive iteration"
    assert captured_kwargs["variables_info"] == ["user_request = hello"]
    assert captured_kwargs["repl_history"] is history
    assert captured_kwargs["iteration"] == "1/3"
    # No TypeError raised; we got a Prediction back.
    assert isinstance(result, dspy.Prediction)


def test_execute_iteration_without_reconstruction_would_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check: calling the inner predictor with empty kwargs does NOT
    deliver the required ``variables_info`` / ``repl_history`` / ``iteration``
    fields. (The real ``dspy.Predict`` raises ``ValueError: No LM is loaded``
    before checking kwargs, but the kwargs are still missing — which is exactly
    why ``_execute_iteration`` must reconstruct them before calling
    ``generate_action``.)"""
    _patch_mlflow(monkeypatch)
    rlm, _events = _make_rlm(max_iterations=3)

    real_inner = rlm.generate_action._inner
    # The inner predictor's signature requires variables_info, repl_history,
    # iteration. Calling it with no kwargs does not deliver any of them.
    try:
        real_inner()
    except (TypeError, ValueError) as exc:
        # Confirms the inner predictor cannot be called with empty kwargs.
        assert isinstance(exc, (TypeError, ValueError))
    else:
        pytest.fail("expected inner predictor to fail when called with empty kwargs")


# ---------------------------------------------------------------------------
# VAL-SEC-016: iteration extracted from args[3]
# ---------------------------------------------------------------------------


def test_execute_iteration_reads_iteration_from_args3(monkeypatch: pytest.MonkeyPatch) -> None:
    """The iteration number in progress events and MLflow span attributes must
    come from ``args[3]`` (the 4th positional arg), NOT from a default of 0.

    The base class calls ``_execute_iteration(repl, variables, history,
    iteration, ...)`` positionally; ``args[3]`` is the 0-indexed iteration int.
    """
    captured_spans: list[dict[str, Any]] = []

    class _CapturingSpan(_FakeSpan):
        def __init__(self, name, span_type=None, attributes=None):
            super().__init__(name, span_type, attributes)
            captured_spans.append(self.record)

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: None,
        start_span=lambda name, span_type=None, attributes=None: _CapturingSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )
    monkeypatch.setattr(
        mlflow_context,
        "mlflow_child_span",
        lambda name, span_type=None, attributes=None, inputs=None: _CapturingSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "set_mlflow_span_outputs",
        lambda span, outputs: span.record.__setitem__("outputs", outputs) if span is not None else None,
    )

    rlm, events = _make_rlm(max_iterations=3)
    rlm.generate_action._inner = MagicMock(return_value=dspy.Prediction(reasoning="ok", code="SUBMIT(response='done')"))
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)

    from dspy.primitives.repl_types import REPLHistory

    # Pass iteration=2 as args[3] (the 4th positional arg). ``_execute_iteration``
    # now runs the generated code; a fake repl returning a plain string keeps the
    # loop going (non-final) so the call completes.
    rlm._execute_iteration(
        _FakeRepl(result="ok"),
        [_FakeVariable("x", "1")],
        REPLHistory(max_output_chars=1500),
        2,  # args[3] = iteration
        {},
        ["response"],
    )

    # Progress events must report iteration=2, NOT 0.
    iteration_events = [e for e in events if "iteration" in e]
    assert iteration_events, "no progress events emitted"
    assert all(e["iteration"] == 2 for e in iteration_events), (
        f"expected all events to report iteration=2, got {[e['iteration'] for e in iteration_events]}"
    )

    # MLflow span attributes must also report iteration=2.
    action_span = next(s for s in captured_spans if s["name"] == "fleet_rlm.rlm_action_generation")
    assert action_span["attributes"]["fleet_rlm.rlm_iteration"] == "2", (
        f"MLflow span rlm_iteration should be '2', got {action_span['attributes']['fleet_rlm.rlm_iteration']}"
    )

    # And generate_action must have been called with iteration="3/3" (args[3]+1).
    call_kwargs = rlm.generate_action._inner.call_args.kwargs
    assert call_kwargs["iteration"] == "3/3"


def test_execute_iteration_iteration_zero_when_args_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """When called with fewer than 4 positional args (e.g. the bypass-init
    test helper calls ``_execute_iteration()`` with no args), iteration falls
    back to 0 rather than raising ``IndexError``."""
    _patch_mlflow(monkeypatch)
    rlm = _make_streaming_rlm_bypass_init()
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)
    # _execute_iteration now runs the action through strip → execute → process.
    # This test only checks the args-length guard (no IndexError), so stub out
    # execution/processing to avoid needing a real repl.
    rlm.generate_action = MagicMock(return_value=dspy.Prediction(reasoning="ok", code="pass"))
    rlm._execute_code = MagicMock(return_value="ok")
    rlm._process_execution_result = MagicMock(return_value=dspy.Prediction(response="done"))

    # Should not raise IndexError; iteration defaults to 0.
    rlm._execute_iteration()


def _make_streaming_rlm_bypass_init() -> Any:
    """Create a ``_StreamingRLM`` without the heavy base-class constructor."""
    instance = _StreamingRLM.__new__(_StreamingRLM)
    instance.action_timeout = 90
    instance.action_max_tokens = None
    instance._consecutive_timeouts = 0
    instance._max_consecutive_timeouts = 2
    instance.generate_action = MagicMock()
    instance.generate_action.current_iteration = 0
    return instance


# ---------------------------------------------------------------------------
# VAL-SEC-017: progress events report increasing iteration across iterations
# ---------------------------------------------------------------------------


def test_progress_events_report_increasing_iteration_across_iterations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Across two ``_execute_iteration`` calls (iteration 0 then iteration 1),
    the emitted progress events must report distinct, increasing iteration
    numbers — NOT all 0 (which would indicate the ``args[3]`` extraction is
    broken)."""
    _patch_mlflow(monkeypatch)
    rlm, events = _make_rlm(max_iterations=3)
    rlm.generate_action._inner = MagicMock(return_value=dspy.Prediction(reasoning="ok", code="SUBMIT(response='done')"))
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)

    from dspy.primitives.repl_types import REPLHistory

    history = REPLHistory(max_output_chars=1500)
    variables = [_FakeVariable("x", "1")]

    # Iteration 0 (first iteration). A fake repl returning a plain string keeps
    # the loop non-final so the call completes.
    rlm._execute_iteration(_FakeRepl(result="ok"), variables, history, 0, {}, ["response"])
    # Iteration 1 (second iteration)
    rlm._execute_iteration(_FakeRepl(result="ok"), variables, history, 1, {}, ["response"])

    iteration_values = sorted({e["iteration"] for e in events if "iteration" in e})
    assert iteration_values == [0, 1], (
        f"expected iteration values {{0, 1}} across two iterations, got {iteration_values}"
    )

    # The first event of each iteration should be the rlm_iteration start event.
    iteration_start_events = [e for e in events if e.get("phase") == "rlm_iteration"]
    assert len(iteration_start_events) >= 2
    assert iteration_start_events[0]["iteration"] == 0
    assert iteration_start_events[1]["iteration"] == 1


# ---------------------------------------------------------------------------
# VAL-SEC-018: _is_parse_error narrowed to JSON-specific markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ValueError("expected output"), False),
        (RuntimeError("invalid state"), False),
        (ValueError("Invalid API key"), False),
        (ValueError("some other error"), False),
        (json.JSONDecodeError("Expecting value", "", 0), True),
        (ValueError("json.decode error: expecting value"), True),
        (ValueError("json parse error"), True),
        (ValueError("malformed json"), True),
        (ValueError("JSON: Expecting value"), True),
    ],
)
def test_is_parse_error_narrowed(exc: Exception, expected: bool) -> None:
    """``_is_parse_error`` must only match JSON-specific markers or
    ``json.JSONDecodeError`` — NOT broad substrings like ``"invalid"`` or
    ``"expected"`` in isolation."""
    assert _StreamingRLM._is_parse_error(exc) is expected


def test_is_parse_error_does_not_match_invalid_api_key() -> None:
    """Regression: ``"Invalid API key"`` is an auth error, NOT a parse error.

    Previously the broad ``"invalid"`` marker caused this to be misclassified
    as a parse error, triggering spurious retries on auth failures.
    """
    assert _StreamingRLM._is_parse_error(ValueError("Invalid API key")) is False
    assert _StreamingRLM._is_parse_error(RuntimeError("invalid state")) is False
    assert _StreamingRLM._is_parse_error(ValueError("expected output")) is False


# ---------------------------------------------------------------------------
# Regression: _execute_iteration must EXECUTE the generated code and build a
# trajectory (not return the bare action Prediction). See plan
# now-let-me-extract-shiny-flame.md — bug introduced in 3c8490f4b.
# ---------------------------------------------------------------------------


class _FakeRepl:
    """Minimal REPL stub recording every executed code string and returning a
    canned result (FinalOutput for SUBMIT, plain string for exploration)."""

    def __init__(self, result: Any) -> None:
        self.executed: list[str] = []
        self._result = result

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.executed.append(code)
        return self._result


def test_execute_iteration_runs_code_and_builds_trajectory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successfully-generated SUBMIT action must be EXECUTED in the REPL and
    produce a ``Prediction`` with a non-None ``trajectory``.

    Before the fix, ``_execute_iteration`` returned the raw action
    ``Prediction`` (reasoning+code only) without calling ``_execute_code`` /
    ``_process_execution_result``. The REPL was never invoked
    (``repl.executed == []``), ``SUBMIT`` was never detected, and the
    ``Prediction`` had no ``trajectory`` → ``has_trajectory=false`` →
    ``RuntimeError("...no trajectory was produced...")`` in
    ``EscalatingFleetModule._run_rlm``.
    """
    from dspy.primitives.code_interpreter import FinalOutput
    from dspy.primitives.repl_types import REPLHistory

    _patch_mlflow(monkeypatch)
    rlm, _events = _make_rlm(max_iterations=3)
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)

    rlm.generate_action._inner = MagicMock(return_value=dspy.Prediction(reasoning="ok", code="SUBMIT(response='done')"))

    repl = _FakeRepl(result=FinalOutput({"response": "done"}))
    history = REPLHistory(max_output_chars=1500)
    variables = [_FakeVariable("user_request", "hello")]

    result = rlm._execute_iteration(repl, variables, history, 0, {}, ["response"])

    # The action's code MUST have been executed in the sandbox REPL.
    assert repl.executed == ["SUBMIT(response='done')"], (
        f"expected _execute_code to run the action code, repl.executed={repl.executed!r}"
    )
    # And the result must carry a real trajectory (the exact check from
    # escalating.py:1129 that gates the RuntimeError).
    assert isinstance(result, dspy.Prediction)
    trajectory = getattr(result, "trajectory", None)
    assert trajectory is not None, "result.Prediction has no trajectory attribute"
    assert len(trajectory) >= 1, f"trajectory should include the SUBMIT step, got {trajectory!r}"
    assert getattr(result, "response", None) == "done"


def test_execute_iteration_strips_fences_and_continues_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-terminal (exploratory) action with fenced code must have its
    fences stripped before REPL execution, and return a ``REPLHistory`` so the
    RLM loop continues to the next iteration (NOT a bare ``Prediction``).
    """
    from dspy.primitives.repl_types import REPLHistory

    _patch_mlflow(monkeypatch)
    rlm, _events = _make_rlm(max_iterations=3)
    monkeypatch.setattr(_StreamingRLM, "_get_bounded_action_lm", lambda self: None)

    rlm.generate_action._inner = MagicMock(
        return_value=dspy.Prediction(
            reasoning="explore the context",
            code="```python\nprint('hi')\n```",
        )
    )

    repl = _FakeRepl(result="hi")  # plain string → non-final, loop continues
    history = REPLHistory(max_output_chars=1500)
    variables = [_FakeVariable("user_request", "hello")]

    result = rlm._execute_iteration(repl, variables, history, 0, {}, ["response"])

    # Fences stripped before execution — the REPL receives bare python.
    assert repl.executed == ["print('hi')"], f"expected fences stripped before execute, repl.executed={repl.executed!r}"
    # Non-final result → REPLHistory (loop continues), not a Prediction.
    assert isinstance(result, REPLHistory), (
        f"non-terminal action should return REPLHistory to continue the loop, got {type(result).__name__}"
    )
