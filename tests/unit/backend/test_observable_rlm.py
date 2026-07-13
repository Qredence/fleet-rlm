"""Live iteration/tool observability at the dspy.RLM-compatible seam."""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.errors import RLMBudgetError
from fleet_rlm.rlm.observable import ObservableRLM, RLMDetail, RLMDetailKind


class _ActionPredictor:
    async def acall(self, **_kwargs: Any) -> dspy.Prediction:
        return dspy.Prediction(
            reasoning="Inspect api_key=secret at /home/daytona/private then use the helper",
            code="value = helper('sample')\nSUBMIT(answer=value)",
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


def test_observable_rlm_remains_a_dspy_rlm() -> None:
    rlm = ObservableRLM("request -> answer", observer=lambda _item: None)
    assert isinstance(rlm, dspy.RLM)


def test_observable_rlm_never_publishes_attachment_skill_or_candidate_bodies() -> None:
    observed: list[RLMDetail] = []
    rlm = ObservableRLM("request -> answer", observer=observed.append)
    read = rlm._instrument_tool(  # noqa: SLF001 - focused public-projection contract
        "read_attachment",
        lambda attachment_id: {
            "ok": True,
            "attachment_id": attachment_id,
            "filename": "private.txt",
            "content": "attachment-secret-body",
        },
    )
    create = rlm._instrument_tool(  # noqa: SLF001 - focused public-projection contract
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
    load = rlm._instrument_tool(  # noqa: SLF001 - focused public-projection contract
        "load_skill",
        lambda skill_id: {"ok": True, "skill_id": skill_id, "instructions": "private-skill-body"},
    )

    read("attachment-id")
    create("text", "private-artifact-body", title="report")
    load("skill-id")

    def _fail_artifact(_kind: str, content: str) -> None:
        raise ValueError(f"could not store {content}")

    failing_create = rlm._instrument_tool(  # noqa: SLF001 - focused public-projection contract
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
    assert "body = '[string:" in public_code

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

    short_observed: list[RLMDetail] = []
    short = ObservableRLM("request -> answer", observer=short_observed.append)
    short_create = short._instrument_tool(  # noqa: SLF001 - short protected-value regression
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
    wrapped = rlm._instrument_tool("helper", lambda: "ok")  # noqa: SLF001

    assert wrapped() == "ok"
    with pytest.raises(RLMBudgetError, match="budget"):
        wrapped()
    assert rlm.tool_budget_exhausted is True
    assert rlm.tool_calls_used == 1
    assert rlm.sub_lm_calls_used == 0
    assert rlm._make_llm_tools(max_workers=8) == {}  # noqa: SLF001
    assert captured_workers == [2]
