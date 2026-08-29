"""P43 direct DSPy 3.3.1 state-semantics contracts without a provider."""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from typing import Any

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput
from dspy.primitives.repl_types import REPLHistory


class SessionRLMContract(dspy.Signature):
    """Exercise the exact Session-shaped Signature planned for P44."""

    request: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    answer: str = dspy.OutputField()


class _SubmittedError(Exception):
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output


class _InProcessInterpreter:
    """Small CodeInterpreter double with one persistent Python namespace.

    It intentionally gives DSPy an ordinary mutable ``tools`` dict. This keeps
    the test at the public ``dspy.RLM.acall(interpreter, ...)`` boundary while
    characterizing DSPy's own tool-map mutation behavior without Daytona,
    Deno, a provider, or credentials.
    """

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.output_fields: list[dict[str, Any]] | None = None
        self.namespace: dict[str, object] = {}
        self.execution_variables: list[dict[str, object]] = []
        self.started = False
        self.shutdown_calls = 0

    def start(self) -> None:
        self.started = True

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.start()
        received = dict(variables or {})
        self.execution_variables.append(received)
        self.namespace.update(self.tools)
        self.namespace.update(received)

        def submit(**output: object) -> None:
            raise _SubmittedError(dict(output))

        self.namespace["SUBMIT"] = submit
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec(code, self.namespace, self.namespace)
        except _SubmittedError as final:
            return FinalOutput(final.output)
        return stdout.getvalue()

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _SubLM:
    """Deterministic sub-LM that exposes invocation-local call-budget resets."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> list[dict[str, str]]:
        self.prompts.append(prompt)
        return [{"text": f"sub:{prompt}"}]


class _SequentialActions:
    """Return deterministic native actions while retaining public REPL inputs."""

    def __init__(self, first_messages: list[dict[str, str]], second_messages: list[dict[str, str]]) -> None:
        self._first_messages = first_messages
        self._second_messages = second_messages
        self.calls = 0
        self.repl_histories: list[REPLHistory] = []
        self.iterations: list[str] = []

    async def acall(self, **kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        history = kwargs["repl_history"]
        assert type(history) is REPLHistory
        self.repl_histories.append(history)
        self.iterations.append(kwargs["iteration"])

        if self.calls == 1:
            return dspy.Prediction(
                reasoning="Inspect the complete committed history and create live state.",
                code=(
                    "assert request == 'request-one'\n"
                    "assert type(history).__name__ == 'History'\n"
                    f"assert history.messages == {self._first_messages!r}\n"
                    "assert llm_query('first-turn') == 'sub:first-turn'\n"
                    "session_value = 42\n"
                    "SUBMIT()"
                ),
            )
        if self.calls == 2:
            return dspy.Prediction(
                reasoning="Repair the typed final output from the same private REPL history.",
                code="SUBMIT(answer=f'first:{session_value}')",
            )
        if self.calls == 3:
            return dspy.Prediction(
                reasoning="Reuse live Python state while inspecting only current durable history.",
                code=(
                    "assert request == 'request-two'\n"
                    "assert session_value == 42\n"
                    "assert type(history).__name__ == 'History'\n"
                    f"assert history.messages == {self._second_messages!r}\n"
                    "assert llm_query('second-turn') == 'sub:second-turn'\n"
                    "SUBMIT(answer=str(session_value) + ':' + history.messages[-1]['answer'])"
                ),
            )
        raise AssertionError(f"unexpected action call {self.calls}")


class _OneAction:
    def __init__(self, code: str) -> None:
        self._code = code

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(reasoning="execute the direct DSPy state contract", code=self._code)


@pytest.mark.asyncio
async def test_p43_history_repl_variable_reuse_and_per_call_resets() -> None:
    """Prove the P43 in-process facts against the literal certified release."""
    from fleet_rlm.rlm._dspy_compat import CERTIFIED_DSPY_VERSION

    assert CERTIFIED_DSPY_VERSION == dspy.__version__ == "3.3.1"
    first_messages = [
        {"request": "opening request\nwith a second line", "answer": "first committed answer"},
        {"request": "follow-up 1", "answer": "answer 1"},
        {"request": "follow-up 2", "answer": "answer 2"},
        {"request": "middle marker", "answer": "middle answer Ω"},
        {"request": "follow-up 4", "answer": "answer 4"},
        {"request": "follow-up 5", "answer": "answer 5"},
        {"request": "follow-up 6", "answer": "answer 6"},
        {"request": "follow-up 7", "answer": "answer 7"},
        {"request": "latest first-turn request", "answer": "latest first-turn answer"},
    ]
    second_messages = [
        *first_messages,
        {"request": "second-turn request", "answer": "latest second-turn answer"},
    ]
    first_history = dspy.History(messages=first_messages)
    second_history = dspy.History(messages=second_messages)
    sub_lm = _SubLM()
    actions = _SequentialActions(first_messages, second_messages)
    interpreter = _InProcessInterpreter()
    rlm = dspy.RLM(
        SessionRLMContract,
        max_iters=2,
        max_llm_calls=1,
        max_output_chars=31,
        sub_lm=sub_lm,  # type: ignore[arg-type]
    )
    rlm.generate_action = actions

    try:
        first = await rlm.acall(interpreter, request="request-one", history=first_history)
        assert interpreter.namespace["history"] is first_history
        second = await rlm.acall(interpreter, request="request-two", history=second_history)
    finally:
        # Direct DSPy must leave lifecycle ownership with the caller.
        assert interpreter.shutdown_calls == 0
        interpreter.shutdown()

    assert type(rlm) is dspy.RLM
    assert first.answer == "first:42"
    assert second.answer == "42:latest second-turn answer"
    assert first.trajectory[0]["output"].startswith("[Error] Missing output fields")
    assert len(second.trajectory) == 1
    assert interpreter.execution_variables == [
        {"request": "request-one", "history": first_history},
        {"request": "request-one", "history": first_history},
        {"request": "request-two", "history": second_history},
    ]

    # The same interpreter holds the Python variable, but DSPy's private history
    # and both native budgets start again at the first action of the next call.
    assert [len(history.entries) for history in actions.repl_histories] == [0, 1, 0]
    assert actions.repl_histories[0] is not actions.repl_histories[2]
    assert [history.max_output_chars for history in actions.repl_histories] == [31, 31, 31]
    assert actions.iterations == ["1/2", "2/2", "1/2"]
    assert "[Error] Missing output fields" in actions.repl_histories[1].entries[0].output
    assert actions.repl_histories[2].entries == []
    assert sub_lm.prompts == ["first-turn", "second-turn"]


@pytest.mark.asyncio
async def test_p43_dspy_tool_map_accumulates_and_old_python_aliases_remain_callable() -> None:
    """Characterize P43.5: direct DSPy only updates a caller-owned tool map."""
    assert dspy.__version__ == "3.3.1"
    calls: list[str] = []

    def old_tool(*, label: str) -> str:
        calls.append(f"old:{label}")
        return f"old:{label}"

    def new_tool(*, label: str) -> str:
        calls.append(f"new:{label}")
        return f"new:{label}"

    interpreter = _InProcessInterpreter()
    first = dspy.RLM(SessionRLMContract, max_iters=1, tools=[old_tool])
    first.generate_action = _OneAction(
        "old_alias = old_tool\nassert old_alias(label='before') == 'old:before'\nSUBMIT(answer='first')"
    )
    second = dspy.RLM(SessionRLMContract, max_iters=1, tools=[new_tool])
    second.generate_action = _OneAction(
        "assert old_alias(label='after') == 'old:after'\n"
        "assert new_tool(label='new') == 'new:new'\n"
        "SUBMIT(answer='second')"
    )

    try:
        first_prediction = await first.acall(interpreter, request="first", history=dspy.History(messages=[]))
        assert set(interpreter.tools) == {"llm_query", "llm_query_batched", "old_tool"}
        second_prediction = await second.acall(interpreter, request="second", history=dspy.History(messages=[]))
    finally:
        assert interpreter.shutdown_calls == 0
        interpreter.shutdown()

    assert first_prediction.answer == "first"
    assert second_prediction.answer == "second"
    assert {"llm_query", "llm_query_batched", "old_tool", "new_tool"}.issubset(interpreter.tools)
    assert calls == ["old:before", "old:after", "new:new"]
