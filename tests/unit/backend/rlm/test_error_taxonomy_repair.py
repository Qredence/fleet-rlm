"""DSPy 3.3.1 interpreter error taxonomy and repair contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError
from fleet_rlm.daytona.interpreter import BackendExecutionResult, DaytonaCodeInterpreter
from fleet_rlm.rlm.result import RunNoProgressError


class _ScriptBackend:
    def __init__(self, *results: BackendExecutionResult | str) -> None:
        self.results = list(results)
        self.calls = 0

    def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult | str:
        del code, variables
        self.calls += 1
        result = self.results.pop(0)
        return result

    def close(self) -> None:
        return None


def test_recoverable_backend_error_is_native_code_execution_error() -> None:
    backend = _ScriptBackend(BackendExecutionResult(error="NameError: missing", error_category="NameError"))
    interpreter = DaytonaCodeInterpreter(backend=backend)

    from dspy.primitives.code_interpreter import CodeExecutionError

    with pytest.raises(CodeExecutionError, match="NameError: missing") as caught:
        interpreter.execute("missing + 1")

    assert not isinstance(caught.value, DaytonaAdapterError)
    assert backend.calls == 1


def test_native_rlm_reinjects_recoverable_error_and_accepts_corrected_action() -> None:
    class Actions:
        def __init__(self) -> None:
            self.calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            if self.calls == 1:
                return dspy.Prediction(reasoning="try the broken expression", code="missing + 1")
            return dspy.Prediction(reasoning="submit the corrected result", code="SUBMIT(answer='ok')")

    backend = _ScriptBackend(
        BackendExecutionResult(error="NameError: missing", error_category="NameError"),
        BackendExecutionResult(final={"answer": "ok"}),
    )
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = dspy.RLM("request -> answer: str", max_iters=2)
    actions = Actions()
    rlm.generate_action = actions

    try:
        prediction = asyncio.run(rlm.acall(interpreter, request="repair"))
    finally:
        interpreter.shutdown()

    assert prediction.answer == "ok"
    assert actions.calls == 2
    assert prediction.trajectory[0]["output"].startswith("[Error] NameError: missing")


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (" \n\t", "empty_code"),
        ("x" * 9, "code_too_large"),
    ],
)
def test_empty_and_oversized_code_raise_recoverable_error_without_backend_execution(
    code: str,
    category: str,
) -> None:
    backend = _ScriptBackend("unexpected")
    kwargs = {"max_code_chars": 8} if category == "code_too_large" else {}
    interpreter = DaytonaCodeInterpreter(backend=backend, **kwargs)

    from dspy.primitives.code_interpreter import CodeExecutionError

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute(code)

    assert caught.value.category == category
    assert backend.calls == 0


def test_intermediate_code_cap_is_inclusive() -> None:
    backend = _ScriptBackend("accepted")
    interpreter = DaytonaCodeInterpreter(backend=backend, max_code_chars=8)

    assert interpreter.execute("12345678") == "accepted"
    assert backend.calls == 1


def test_terminal_interpreter_error_stops_native_rlm_without_repair_or_extract() -> None:
    from dspy.primitives.code_interpreter import CodeInterpreterError

    class TerminalBackend:
        calls = 0

        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            self.calls += 1
            raise CodeInterpreterError("protocol is corrupt")

        def close(self) -> None:
            return None

    class Actions:
        calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            return dspy.Prediction(reasoning="execute", code="print('x')")

    backend = TerminalBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = dspy.RLM("request -> answer: str", max_iters=3)
    actions = Actions()
    rlm.generate_action = actions
    extract_calls = 0

    async def extract(**_kwargs: Any) -> dspy.Prediction:
        nonlocal extract_calls
        extract_calls += 1
        return dspy.Prediction(answer="should not run")

    rlm.extract = extract
    try:
        with pytest.raises(CodeInterpreterError, match="protocol is corrupt"):
            asyncio.run(rlm.acall(interpreter, request="stop"))
    finally:
        interpreter.shutdown()

    assert backend.calls == 1
    assert actions.calls == 1
    assert extract_calls == 0


def test_direct_native_recoverable_error_is_sanitized_and_reclassified() -> None:
    from dspy.primitives.code_interpreter import CodeExecutionError

    class NativeBackend:
        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            raise CodeExecutionError(
                "GET https://canary.invalid/v1 token=canary-fake-token /Volumes/operator/private.py \x1b[31mboom\x1b[0m"
            )

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=NativeBackend())

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("broken")

    assert caught.value.category == "execution_error"
    assert "provider.invalid" not in str(caught.value)
    assert "canary-fake-token" not in str(caught.value)
    assert "/Volumes/operator" not in str(caught.value)
    assert "\x1b" not in str(caught.value)


def test_direct_native_terminal_error_is_sanitized_and_stops() -> None:
    from dspy.primitives.code_interpreter import CodeInterpreterError

    class NativeBackend:
        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            raise CodeInterpreterError(
                "protocol https://provider.invalid/private /root/secret.py \x1b[31mcorrupt\x1b[0m"
            )

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=NativeBackend())

    with pytest.raises(CodeInterpreterError) as caught:
        interpreter.execute("broken")

    assert caught.value.category == "CodeInterpreterError"
    assert "provider.invalid" not in str(caught.value)
    assert "/root/secret.py" not in str(caught.value)
    assert "\x1b" not in str(caught.value)


def test_broad_daytona_failure_stays_fleet_error_without_repair() -> None:
    class ProviderBackend:
        calls = 0

        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            self.calls += 1
            raise DaytonaAdapterError("broker transport failed", cause_type="BrokerExecutionError")

        def close(self) -> None:
            return None

    backend = ProviderBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)

    from dspy.primitives.code_interpreter import CodeInterpreterError

    with pytest.raises(DaytonaAdapterError):
        interpreter.execute("print('never')")

    assert not isinstance(DaytonaAdapterError("x"), CodeInterpreterError)
    assert backend.calls == 1


def test_no_progress_has_one_native_repair_then_terminal_bound() -> None:
    from dspy.primitives.code_interpreter import CodeExecutionError

    from fleet_rlm.daytona.interpreter import InProcessInterpreterBackend

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    assert interpreter.execute("_out = 'same'") == "same"
    with pytest.raises(CodeExecutionError, match="Repeated interpreter action produced no progress"):
        interpreter.execute("_out = 'same'")
    with pytest.raises(RunNoProgressError, match="repeated tool calls made no progress"):
        interpreter.execute("_out = 'same'")
    assert interpreter.execute("_out = 'different'") == "different"


def test_repair_text_is_bounded_and_safe_for_native_context() -> None:
    private = (
        "Traceback (most recent call last):\n"
        '  File "/Users/operator/private.py", line 7\n'
        "api_key=FAKE-CANARY-key-0000 "
        "https://canary.invalid/v1 "
        "/Volumes/operator/secret.py "
        "__FLEET_FINAL_OUTPUT__ "
        "\x1b[31munsafe\x1b[0m"
    )
    backend = _ScriptBackend(BackendExecutionResult(error=private, error_category="RuntimeError"))
    interpreter = DaytonaCodeInterpreter(backend=backend)

    from dspy.primitives.code_interpreter import CodeExecutionError

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("broken")

    text = str(caught.value)
    assert len(text) <= 512
    assert "FAKE-CANARY-key-0000" not in text
    assert "operator/private.py" not in text
    assert "__FLEET_FINAL_OUTPUT__" not in text
    assert "\x1b" not in text


def test_provider_failure_trace_keeps_category_but_not_private_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.observability import tracing as turn_tracing
    from fleet_rlm.rlm._dspy_compat import _RLMTraceCallback

    captured: dict[str, object] = {}

    class Span:
        def set_inputs(self, _payload: dict[str, object]) -> None:
            return None

        def set_outputs(self, payload: dict[str, object]) -> None:
            captured["outputs"] = payload

        def set_attributes(self, payload: dict[str, object]) -> None:
            captured["attributes"] = payload

        def set_status(self, _status: str) -> None:
            return None

    class SpanContext:
        def __enter__(self) -> Span:
            return Span()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "mlflow",
        SimpleNamespace(
            get_current_active_span=lambda: Span(),
            start_span=lambda **_kwargs: SpanContext(),
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "mlflow.entities",
        SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", LLM="LLM")),
    )

    root = SimpleNamespace(model="root-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=SimpleNamespace(model="sub-model"))
    error = ProviderRequestError(
        "POST https://canary.invalid/v1 token=canary-fake-token /Users/operator/private",
        cause_type="ProviderRequestError",
        status_code=503,
    )
    token = turn_tracing._fleet_trace_active.set(True)
    try:
        callback.on_lm_start("call-1", root, {"prompt": "safe"})
        callback.on_lm_end("call-1", [], error)
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    outputs = str(captured["outputs"])
    attributes = str(captured["attributes"])
    assert "provider_5xx" in outputs
    assert "canary-fake-token" not in outputs
    assert "password" not in outputs
    assert "canary.invalid" not in outputs
    assert "/Users/operator" not in outputs
    assert "canary-fake-token" not in attributes
