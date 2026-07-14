"""Live iteration/tool observability at the dspy.RLM-compatible seam."""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.errors import RunBudgetError
from fleet_rlm.rlm.observable import ObservableRLM, RLMDetail, RLMDetailKind


class _ActionPredictor:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(
            reasoning="Inspect api_key=secret at /home/daytona/private then use the helper",
            code="value = helper('sample')\nSUBMIT(answer=value)",
        )


class _RepairAfterExecutionErrorPredictor:
    def __init__(self) -> None:
        self.calls = 0

    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        self.calls += 1
        if self.calls == 1:
            return dspy.Prediction(
                reasoning="Read the metric.",
                code="metrics = {'precision': 0.9}\nprint(metrics['prec'])",
            )
        return dspy.Prediction(
            reasoning="The previous key was incorrect; use the available precision key.",
            code="SUBMIT(answer='precision=0.9')",
        )


@pytest.mark.asyncio
async def test_observable_rlm_streams_sanitized_iteration_and_tool_details() -> None:
    observed: list[RLMDetail] = []

    def helper(value: str) -> str:
        return f"done:{value}"

    rlm = ObservableRLM(
        "request -> answer",
        max_iterations=2,
        tools=[helper],
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        observer=observed.append,
    )
    rlm.generate_action = _ActionPredictor()

    prediction = await rlm.aforward(request="hello")

    assert prediction.answer == "done:sample"
    assert [item.kind for item in observed] == [
        RLMDetailKind.STEP_STARTED,
        RLMDetailKind.REASONING,
        RLMDetailKind.CODE,
        RLMDetailKind.TOOL_STARTED,
        RLMDetailKind.TOOL_COMPLETED,
        RLMDetailKind.OUTPUT,
        RLMDetailKind.STEP_FINISHED,
    ]
    reasoning = next(item for item in observed if item.kind is RLMDetailKind.REASONING)
    assert "secret" not in str(reasoning.payload)
    tool_start = next(item for item in observed if item.kind is RLMDetailKind.TOOL_STARTED)
    tool_end = next(item for item in observed if item.kind is RLMDetailKind.TOOL_COMPLETED)
    assert tool_start.payload["tool_call_id"] == tool_end.payload["tool_call_id"]
    assert tool_start.payload["tool_name"] == "helper"
    assert "/home/" not in str(observed)


@pytest.mark.asyncio
async def test_observable_rlm_recovers_from_user_code_error_on_the_next_iteration() -> None:
    observed: list[RLMDetail] = []
    rlm = ObservableRLM(
        "request -> answer",
        max_iterations=2,
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        observer=observed.append,
    )
    rlm.generate_action = _RepairAfterExecutionErrorPredictor()

    prediction = await rlm.aforward(request="summarize metrics")

    assert prediction.answer == "precision=0.9"
    outputs = [item.payload["output"] for item in observed if item.kind is RLMDetailKind.OUTPUT]
    assert any("'prec'" in output for output in outputs)
    assert sum(item.kind is RLMDetailKind.STEP_STARTED for item in observed) == 2


def test_observable_rlm_remains_a_dspy_rlm() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)
    assert isinstance(rlm, dspy.RLM)


def test_observable_rlm_never_publishes_attachment_skill_or_candidate_bodies() -> None:
    observed: list[RLMDetail] = []
    rlm = ObservableRLM("request -> answer", observer=observed.append)
    read = rlm.instrument_tool(
        "read_attachment",
        lambda attachment_id: {
            "ok": True,
            "attachment_id": attachment_id,
            "filename": "private.txt",
            "content": "attachment-secret-body",
        },
    )
    create = rlm.instrument_tool(
        "create_artifact",
        lambda kind, content, title=None: {
            "ok": True,
            "artifact_candidate_id": "private-candidate-id",
            "checksum_sha256": "private-checksum",
            "kind": kind,
            "title": title,
            "byte_size": len(content),
        },
    )
    load = rlm.instrument_tool(
        "load_skill",
        lambda skill_id: {"ok": True, "skill_id": skill_id, "instructions": "private-skill-body"},
    )

    read("attachment-id")
    create("text", "private-artifact-body", title="report")
    load("skill-id")

    def _fail_artifact(_kind: str, content: str) -> None:
        raise ValueError(f"could not store {content}")

    failing_create = rlm.instrument_tool(
        "create_artifact",
        _fail_artifact,
    )
    with pytest.raises(ValueError):
        failing_create("text", "private-failed-artifact-body")

    public_code = rlm._sanitize_code(  # noqa: SLF001 - focused public-projection contract
        "body = 'private-artifact-body'; emit = create_artifact; emit('text', body); llm_query('private prompt body')"
    )

    public = str([item.payload for item in observed])
    for forbidden in (
        "attachment-secret-body",
        "private-artifact-body",
        "private-candidate-id",
        "private-checksum",
        "private-skill-body",
        "private prompt body",
        "private-failed-artifact-body",
    ):
        assert forbidden not in public
        assert forbidden not in public_code
    assert "body = '[private]'" in public_code
    assert "emit('text', body)" in public_code


def test_observable_rlm_code_projection_preserves_harmless_literals_and_f_strings() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)

    public_code = rlm._sanitize_code(  # noqa: SLF001 - focused public-projection contract
        "digit = '7'\nlabel = f'digit={digit}'\nprint(label)\nSUBMIT(answer=digit)"
    )

    assert "digit = '7'" in public_code
    assert "label = f'digit={digit}'" in public_code
    assert "print(label)" in public_code
    assert "[string:" not in public_code


def test_observable_rlm_code_projection_redacts_only_protected_call_arguments() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)

    public_code = rlm._sanitize_code(  # noqa: SLF001 - focused public-projection contract
        "\n".join(
            (
                "question = '955th digit'",
                "answer = llm_query(f'private prompt: {question}')",
                "answers = llm_query_batched(['private one', f'private {question}'])",
                "create_artifact('text', f'private artifact: {answer}', title='Pi report')",
                "print('7')",
            )
        )
    )

    assert "question = '955th digit'" in public_code
    assert "llm_query('[redacted-subquery-prompt]')" in public_code
    assert "llm_query_batched(['[redacted-subquery-prompts]'])" in public_code
    assert "'[redacted-artifact-content]'" in public_code
    assert "title='Pi report'" in public_code
    assert "print('7')" in public_code
    assert "private prompt" not in public_code
    assert "private one" not in public_code
    assert "private artifact" not in public_code


def test_observable_rlm_code_projection_redacts_private_values_secrets_and_paths_safely() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)
    rlm._fleet_private_tokens.add("previously private value")  # noqa: SLF001

    public_code = rlm._sanitize_code(  # noqa: SLF001 - focused public-projection contract
        "\n".join(
            (
                "private = 'previously private value'",
                "credential = 'api_key=super-secret-value'",
                "location = '/home/daytona/private/file.txt'",
                "message = f'safe prefix {private}'",
            )
        )
    )

    assert "previously private value" not in public_code
    assert "super-secret-value" not in public_code
    assert "/home/daytona" not in public_code
    assert "private = '[private]'" in public_code
    assert "credential = '[redacted]'" in public_code
    assert "location = '[path]'" in public_code
    assert "message = f'safe prefix {private}'" in public_code

    rlm._remember_private_values(  # noqa: SLF001 - focused escaped-output regression
        "load_skill",
        (),
        {},
        {"instructions": "line one\nline two secret"},
    )
    assert rlm._fleet_private_tokens  # noqa: SLF001 - activates deny-by-default detail projection
    assert "line one" not in rlm._public_reasoning(  # noqa: SLF001
        "line one and a conclusion"
    )
    assert "line one" not in rlm._public_interpreter_output(  # noqa: SLF001
        {"instructions": "line one\nline two secret"}
    )


def test_observable_rlm_code_projection_redacts_turn_inputs_and_protected_results() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)

    rlm._remember_input_values({"request": "private user-provided phrase"})  # noqa: SLF001
    rlm._remember_private_values(  # noqa: SLF001
        "llm_query",
        ("safe prompt",),
        {},
        "private sub-lm result",
    )

    public_code = rlm._sanitize_code(  # noqa: SLF001 - focused public-projection contract
        "user = 'private user-provided phrase'\nresult = 'private sub-lm result'"
    )

    assert "private user-provided phrase" not in public_code
    assert "private sub-lm result" not in public_code
    assert "user = '[private]'" in public_code
    assert "result = '[private]'" in public_code

    short_observed: list[RLMDetail] = []
    short = ObservableRLM("request -> answer", observer=short_observed.append)
    short_create = short.instrument_tool(
        "create_artifact",
        lambda _kind, content: (_ for _ in ()).throw(ValueError(f"failed {content}")),
    )
    with pytest.raises(ValueError):
        short_create("text", "PIN7")
    assert short._fleet_protected_data_accessed is True  # noqa: SLF001
    assert "PIN7" not in short._public_interpreter_output("PIN7")  # noqa: SLF001
    short_failed = next(item for item in short_observed if item.kind is RLMDetailKind.TOOL_FAILED)
    assert short_failed.payload["error"] == "Protected tool failed"


def test_observable_rlm_enforces_tool_and_sub_lm_concurrency_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_workers: list[int] = []

    def _fake_make_tools(_self: Any, max_workers: int = 8) -> dict[str, Any]:
        captured_workers.append(max_workers)
        return {}

    monkeypatch.setattr(dspy.RLM, "_make_llm_tools", _fake_make_tools)
    rlm = ObservableRLM(
        "request -> answer",
        max_tool_calls=1,
        max_sub_lm_concurrency=2,
    )
    wrapped = rlm.instrument_tool("helper", lambda: "ok")

    assert wrapped() == "ok"
    with pytest.raises(RunBudgetError, match="budget"):
        wrapped()
    assert rlm.tool_budget_exhausted is True
    assert rlm.tool_calls_used == 1
    assert rlm.sub_lm_calls_used == 0
    assert rlm._make_llm_tools(max_workers=8) == {}  # noqa: SLF001
    assert captured_workers == [2]
