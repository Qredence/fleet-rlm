"""P43.6 direct DSPy custom-Skill History contract without a provider."""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from typing import Any

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput


class FleetStyleSkillHistorySignature(dspy.Signature):
    """Model the P44 custom Skill shape without changing a production Signature."""

    request: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    findings: list[str] = dspy.OutputField()
    metrics: list[dict] = dspy.OutputField()
    anomalies: list[str] = dspy.OutputField()


class _SubmittedError(Exception):
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output


class _InProcessInterpreter:
    """Minimal caller-owned interpreter for the native RLM boundary."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.output_fields: list[dict[str, Any]] | None = None
        self.namespace: dict[str, object] = {}

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.namespace.update(self.tools)
        self.namespace.update(variables or {})

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
        return None


class _SequentialActions:
    """Submit structured Skill results while checking the injected History records."""

    def __init__(
        self,
        initial_history: list[dict[str, str]],
        committed_history: list[dict[str, str]],
    ) -> None:
        self._initial_history = initial_history
        self._committed_history = committed_history
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        if self.calls == 1:
            return dspy.Prediction(
                reasoning="Inspect canonical history and emit the full structured Skill result.",
                code=(
                    "assert type(history).__name__ == 'History'\n"
                    f"assert history.messages == {self._initial_history!r}\n"
                    "assert all(set(record) == {'request', 'answer'} for record in history.messages)\n"
                    "assert request == 'Analyze the first quarter.'\n"
                    "assert session_context == {'session_id': 'session-43'}\n"
                    "assert skill_cards == [{'name': 'data-analysis'}]\n"
                    "assert attachments == []\n"
                    "SUBMIT(\n"
                    "    answer='Revenue rose 10%.',\n"
                    "    findings=['Revenue rose from 100 to 110.'],\n"
                    "    metrics=[{'name': 'revenue_growth', 'value': 0.1}],\n"
                    "    anomalies=['No anomaly detected.'],\n"
                    ")"
                ),
            )
        if self.calls == 2:
            return dspy.Prediction(
                reasoning="Verify that the next History record remains canonical.",
                code=(
                    f"assert history.messages == {self._committed_history!r}\n"
                    "assert history.messages[-1] == {\n"
                    "    'request': 'Analyze the first quarter.',\n"
                    "    'answer': 'Revenue rose 10%.',\n"
                    "}\n"
                    "assert all(set(record) == {'request', 'answer'} for record in history.messages)\n"
                    "assert request == 'What was the result?'\n"
                    "SUBMIT(\n"
                    "    answer='The result was a 10% revenue increase.',\n"
                    "    findings=['The first-quarter result was retained.'],\n"
                    "    metrics=[{'name': 'revenue_growth', 'value': 0.1}],\n"
                    "    anomalies=[],\n"
                    ")"
                ),
            )
        raise AssertionError(f"unexpected action call {self.calls}")


@pytest.mark.asyncio
async def test_p43_6_custom_skill_history_preserves_structured_outputs_and_canonical_records() -> None:
    """Prove a planned custom Skill Signature can receive canonical Fleet History."""
    from fleet_rlm.rlm.dspy_contract import CERTIFIED_DSPY_VERSION

    assert CERTIFIED_DSPY_VERSION == dspy.__version__ == "3.3.1"
    assert list(FleetStyleSkillHistorySignature.output_fields) == ["answer", "findings", "metrics", "anomalies"]

    initial_records = [
        {"request": "What was last quarter's revenue?", "answer": "Revenue was 100."},
    ]
    first_request = "Analyze the first quarter."
    first_history = dspy.History(messages=initial_records)
    committed_records = [
        *initial_records,
        {"request": first_request, "answer": "Revenue rose 10%."},
    ]
    actions = _SequentialActions(initial_records, committed_records)
    rlm = dspy.RLM(FleetStyleSkillHistorySignature, max_iters=1)
    rlm.generate_action = actions
    interpreter = _InProcessInterpreter()

    first = await rlm.acall(
        interpreter,
        request=first_request,
        history=first_history,
        session_context={"session_id": "session-43"},
        skill_cards=[{"name": "data-analysis"}],
        attachments=[],
    )

    assert first.answer == "Revenue rose 10%."
    assert first.findings == ["Revenue rose from 100 to 110."]
    assert first.metrics == [{"name": "revenue_growth", "value": 0.1}]
    assert first.anomalies == ["No anomaly detected."]
    assert first_history.messages == initial_records

    # Fleet, not DSPy, owns this commit projection. The full structured result
    # stays in the prediction; the next History receives only request/answer.
    committed_history = dspy.History(messages=[*initial_records, {"request": first_request, "answer": first.answer}])
    assert committed_history.messages == committed_records
    assert all(set(record) == {"request", "answer"} for record in committed_history.messages)
    assert all(
        "findings" not in record and "metrics" not in record and "anomalies" not in record
        for record in committed_history.messages
    )

    second = await rlm.acall(
        interpreter,
        request="What was the result?",
        history=committed_history,
        session_context={"session_id": "session-43"},
        skill_cards=[{"name": "data-analysis"}],
        attachments=[],
    )

    assert actions.calls == 2
    assert second.answer == "The result was a 10% revenue increase."
    assert second.findings == ["The first-quarter result was retained."]
    assert second.metrics == [{"name": "revenue_growth", "value": 0.1}]
    assert second.anomalies == []
