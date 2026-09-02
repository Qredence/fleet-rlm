"""P43.7 fallback contracts for the committed Session History transport."""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from typing import Any

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.sessions.history_transport import (
    CommittedSessionHistory,
    committed_session_history_payload,
)


class _SubmittedError(Exception):
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output


class _InProcessInterpreter:
    """Mirror DSPy's SandboxSerializable injection contract without a provider."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.namespace: dict[str, object] = {}
        self.executed_code: list[str] = []

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.namespace.update(self.tools)
        received = dict(variables or {})
        if "_raw_history" in received and isinstance(received["_raw_history"], bytes):
            # Reproduce RLM._prepare_serializable_vars payload decoding.
            try:
                received["_raw_history"] = received["_raw_history"].decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover - defensive parity
                import base64

                received["_raw_history"] = base64.b64encode(received["_raw_history"]).decode("ascii")
        self.namespace.update(received)
        self.executed_code.append(code)

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


def test_transport_diagnostics_redact_complete_conversation_bodies() -> None:
    history = CommittedSessionHistory([{"request": "SECRET_REQUEST_BODY", "answer": "SECRET_ANSWER_BODY"}])

    rendered = repr(history)
    assert rendered == "CommittedSessionHistory(messages=1)"
    assert str(history) == rendered
    assert "SECRET_REQUEST_BODY" not in rendered
    assert "SECRET_ANSWER_BODY" not in rendered


def test_transport_rejects_non_canonical_records_without_truncation() -> None:
    for bad in (
        [{"request": "r", "answer": "a", "extra": "x"}],
        [{"request": 1, "answer": "a"}],
        [{"prompt": "r", "answer": "a"}],
        [{"request": "r"}],
        ["not-a-record"],  # type: ignore[list-item]
    ):
        with pytest.raises(ValueError, match="committed Session History"):
            CommittedSessionHistory(bad)


def test_transport_round_trips_complete_unicode_multiline_records_in_sandbox() -> None:
    records = [
        {"request": "first Ω漢字🧪\nsecond line", "answer": "café — résumé ✓"},
        *({"request": f"bulk {ordinal}", "answer": f"reply {ordinal}\npara {ordinal}"} for ordinal in range(200)),
    ]
    history = CommittedSessionHistory(records)
    assert committed_session_history_payload(history) == records
    assert "café" not in history.rlm_preview()
    assert len(history.rlm_preview()) <= 500

    interpreter = _InProcessInterpreter()
    # Exactly the statements DSPy's _prepare_serializable_vars executes.
    setup = history.sandbox_setup()
    assignment = history.sandbox_assignment("history", "_raw_history")
    assert "_raw_history" in assignment and "history.messages" not in assignment
    interpreter.execute("\n".join((setup, assignment)), variables={"_raw_history": history.to_sandbox()})

    restored = interpreter.namespace["history"]
    assert type(restored).__name__ == "_FleetCommittedHistory"
    assert list(restored.messages) == records
    assert restored.messages[0]["answer"] == "café — résumé ✓"
    assert "_fleet_load_committed_history" not in interpreter.namespace


@pytest.mark.asyncio
async def test_native_rlm_acall_sees_complete_history_through_the_transport() -> None:
    records = [
        {"request": "opening request", "answer": "opening answer"},
        {"request": "latest request", "answer": "latest answer"},
    ]
    history = CommittedSessionHistory(records)

    class SessionRLMContract(dspy.Signature):
        request: str = dspy.InputField()
        history: dspy.History = dspy.InputField()
        answer: str = dspy.OutputField()

    class _Actions:
        def __init__(self) -> None:
            self.calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            return dspy.Prediction(
                reasoning="Read the complete committed conversation from the transported value.",
                code=(
                    "assert type(history).__name__ == '_FleetCommittedHistory'\n"
                    f"assert history.messages == {records!r}\n"
                    "assert history.messages[-1]['answer'] == 'latest answer'\n"
                    "SUBMIT(answer=history.messages[-1]['answer'])"
                ),
            )

    actions = _Actions()
    rlm = dspy.RLM(SessionRLMContract, max_iters=1)
    rlm.generate_action = actions
    interpreter = _InProcessInterpreter()

    prediction = await rlm.acall(interpreter, request="current", history=history)

    assert actions.calls == 1
    assert prediction.answer == "latest answer"
    assert any("_raw_history" in code for code in interpreter.executed_code)


def test_history_materializes_before_nested_provider_404_failure() -> None:
    """A provider failure must not be attributed to the committed-history transport."""
    history = CommittedSessionHistory([{"request": "prior", "answer": "settled"}])
    interpreter = _InProcessInterpreter()
    interpreter.execute(
        "\n".join((history.sandbox_setup(), history.sandbox_assignment("history", "_raw_history"))),
        variables={"_raw_history": history.to_sandbox()},
    )
    assert interpreter.namespace["history"].messages == [{"request": "prior", "answer": "settled"}]

    class _ProviderNotFoundError(Exception):
        status_code = 404

    try:
        raise RuntimeError("LMUnsupportedModelError: Error code: 404") from _ProviderNotFoundError()
    except RuntimeError as raised:
        assert normalize_turn_failure(raised).cause_type == "provider_not_found"
