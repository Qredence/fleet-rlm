"""Unit tests for the decomposed recursive workspace phases and orchestrator."""

from __future__ import annotations

import json
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.modules.workspace import RecursiveWorkspaceModule
from fleet_rlm.runtime.modules.workspace_phases import (
    ExecuteSubqueriesPhase,
    append_failure_signals,
    classify_subquery_failures,
    classify_verification_failures,
    compact_for_signature,
    has_current_source_context,
    is_missing_source_failure,
    merge_failure_signals,
    requires_current_source_context,
)


class TestPureHelpers:
    def test_compact_for_signature_preserves_short_text(self) -> None:
        assert compact_for_signature("short") == "short"

    def test_compact_for_signature_truncates_long_text(self) -> None:
        text = "a" * 10_000
        compacted = compact_for_signature(text, max_chars=4_000)
        assert len(compacted) < len(text)
        assert "chars omitted" in compacted

    def test_classify_subquery_failures_from_json_status(self) -> None:
        output = json.dumps({"status": "error", "reason": "tool_error"})
        signals = classify_subquery_failures([output])
        assert "output[0]:status=error" in signals
        assert "output[0]:reason=tool_error" in signals

    def test_classify_subquery_failures_clean_output(self) -> None:
        assert classify_subquery_failures(["all good, 42"]) == []

    def test_classify_verification_failures_status(self) -> None:
        signals = classify_verification_failures(
            status="needs_human_review",
            verified_summary="summary",
            missing_evidence=[],
            contradictions=[],
            rationale="",
        )
        assert signals == ["verification:status=needs_human_review"]

    def test_merge_failure_signals_dedupes_and_sorts(self) -> None:
        assert merge_failure_signals(["b", "a"], ["a", "c"]) == ["a", "b", "c"]

    def test_append_failure_signals_footer(self) -> None:
        result = append_failure_signals("summary", ["sig1"])
        assert result.startswith("summary")
        assert "sig1" in result

    def test_requires_current_source_context_detects_urls(self) -> None:
        assert requires_current_source_context(user_request="summarize https://example.com/doc", context="")
        assert not requires_current_source_context(user_request="what is 2+2", context="")

    def test_has_current_source_context(self) -> None:
        assert has_current_source_context("/workspace/repo/main.py")
        assert has_current_source_context("--- Document fetched from https://x ---\nbody")
        assert not has_current_source_context("")
        assert not has_current_source_context("a short note")

    def test_is_missing_source_failure(self) -> None:
        assert is_missing_source_failure(
            user_request="analyze the codebase architecture",
            context="",
            failure_signals=["output[0]:workspace is empty"],
        )
        assert not is_missing_source_failure(
            user_request="what is 2+2",
            context="",
            failure_signals=["output[0]:workspace is empty"],
        )


class TestExecuteSubqueriesPhase:
    def test_serial_execution_formats_success_and_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate

        responses = iter(
            [
                {"status": "ok", "answer": "fine"},
                {"status": "error", "reason": "tool_error", "error": "boom"},
            ]
        )
        monkeypatch.setattr(rlm_delegate, "delegate_to_rlm", lambda **kwargs: next(responses))

        phase = ExecuteSubqueriesPhase(interpreter=object())
        result = phase(subqueries=["q1", "q2"], context="ctx", mode="serial")

        assert result.outputs[0] == "fine"
        failure_payload = json.loads(result.outputs[1])
        assert failure_payload["status"] == "error"
        assert any("tool_error" in signal for signal in result.failure_signals)

    def test_fan_out_uses_batched_delegation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate

        captured: dict[str, Any] = {}

        def fake_batched(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"status": "ok", "results": [{"answer": "a1"}, {"answer": "a2"}]}

        monkeypatch.setattr(rlm_delegate, "delegate_to_rlm_batched", fake_batched)

        phase = ExecuteSubqueriesPhase(interpreter=object())
        result = phase(subqueries=["q1", "q2"], context="ctx", mode="fan_out")

        assert captured["queries"] == ["q1", "q2"]
        assert result.outputs == ["a1", "a2"]
        assert result.failure_signals == []


class _StubPhase(dspy.Module):
    """Phase stub returning a canned prediction and recording call kwargs."""

    def __init__(self, **fields: Any) -> None:
        super().__init__()
        self._fields = fields
        self.calls: list[dict[str, Any]] = []

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        self.calls.append(kwargs)
        return dspy.Prediction(**self._fields)


def _build_orchestrator_with_stubs(**overrides: Any) -> RecursiveWorkspaceModule:
    module = RecursiveWorkspaceModule.__new__(RecursiveWorkspaceModule)
    dspy.Module.__init__(module)
    module.interpreter = object()
    module.max_passes = 3
    module.max_repair_attempts = 2
    module.subquery_budget = 4
    module.context_budget_chars = 32_000
    module._evidence = None

    module.assemble_phase = overrides.get(
        "assemble_phase",
        _StubPhase(assembled_context="assembled context that is long enough to avoid the fallback path here"),
    )
    module.plan_phase = overrides.get(
        "plan_phase",
        _StubPhase(subqueries=["q1"], decomposition_mode="serial", aggregation_plan="join"),
    )
    module.execute_phase = overrides.get(
        "execute_phase",
        _StubPhase(outputs=["the answer"], failure_signals=[]),
    )
    module.verify_phase = overrides.get(
        "verify_phase",
        _StubPhase(
            verification_status="sufficient",
            verified_summary="verified",
            missing_evidence=[],
            contradictions=[],
            failure_signals=[],
        ),
    )
    module.reflect_phase = overrides.get(
        "reflect_phase",
        _StubPhase(next_action="finalize", revised_plan="plan"),
    )
    module.repair_phase = overrides.get("repair_phase", _StubPhase(repair_subqueries=[]))
    return module


class TestRecursiveWorkspaceOrchestrator:
    def test_sufficient_verification_returns_best_answer(self) -> None:
        module = _build_orchestrator_with_stubs()
        result = module(user_request="what is the answer?")

        assert result.status == "sufficient"
        assert result.answer == "the answer"
        assert result.passes == 1

    def test_subquery_human_review_short_circuits(self) -> None:
        review_output = json.dumps({"status": "needs_human_review", "answer": "partial"})
        module = _build_orchestrator_with_stubs(
            execute_phase=_StubPhase(
                outputs=[review_output],
                failure_signals=classify_subquery_failures([review_output]),
            ),
        )
        result = module(user_request="do something hard")

        assert result.status == "needs_human_review"
        assert "needs_human_review" in result.answer

    def test_failure_signals_trigger_repair_pass(self) -> None:
        failing_output = json.dumps({"status": "error", "reason": "tool_error"})

        class _FlakyExecute(dspy.Module):
            def __init__(self) -> None:
                super().__init__()
                self.call_count = 0

            def forward(self, **kwargs: Any) -> dspy.Prediction:
                self.call_count += 1
                if self.call_count == 1:
                    return dspy.Prediction(
                        outputs=[failing_output],
                        failure_signals=classify_subquery_failures([failing_output]),
                    )
                return dspy.Prediction(outputs=["repaired answer"], failure_signals=[])

        execute_phase = _FlakyExecute()
        repair_phase = _StubPhase(repair_subqueries=["retry q1"])
        module = _build_orchestrator_with_stubs(execute_phase=execute_phase, repair_phase=repair_phase)
        result = module(user_request="compute something")

        assert repair_phase.calls, "repair phase should be invoked on failure signals"
        assert execute_phase.call_count >= 2
        assert result.status == "sufficient"
        assert result.answer == "repaired answer"

    def test_budget_exhausted_after_max_passes(self) -> None:
        module = _build_orchestrator_with_stubs(
            verify_phase=_StubPhase(
                verification_status="needs_more_recursion",
                verified_summary="not enough",
                missing_evidence=["x"],
                contradictions=[],
                failure_signals=[],
            ),
            reflect_phase=_StubPhase(next_action="recurse", revised_plan="dig deeper"),
        )
        result = module(user_request="open ended question")

        assert result.status == "budget_exhausted"
        assert result.passes == 3
