"""Unit tests for RecursiveWorkspaceModule (Level 4 RLM orchestrator).

Verifies:
- Module construction creates 5 inner dspy.RLM sub-modules
- forward() runs the assemble→plan→execute→verify→reflect loop
- Sufficient verification → early return at pass 1
- Repair path → re-executes targeted subqueries
- Budget exhaustion → bounded exit after max_passes
- Evidence is stored across passes via store_evidence bridge
- recursive_workspace tool is discoverable and bound by AgentRuntime
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_prediction(**fields: Any) -> dspy.Prediction:
    return dspy.Prediction(**fields)


def _mock_interpreter() -> MagicMock:
    interp = MagicMock()
    interp._started = True
    interp.sub_lm = None
    interp._host_repository = None
    interp._host_identity = None
    interp._host_run_id = None
    interp._tools = {}
    interp.llm_query = MagicMock(return_value="answer")
    interp.llm_query_batched = MagicMock(return_value=["a"])
    return interp


def _build_module(interp: Any = None, **kwargs: Any) -> Any:
    """Build a RecursiveWorkspaceModule with mocked inner RLMs."""
    from fleet_rlm.runtime.models.builders import RecursiveWorkspaceModule

    with patch("fleet_rlm.runtime.models.builders.create_runtime_rlm") as mock_create:
        mock_create.return_value = MagicMock(spec=dspy.Module)
        module = RecursiveWorkspaceModule(
            interpreter=interp or _mock_interpreter(),
            max_iterations=5,
            max_llm_calls=10,
            **kwargs,
        )
    return module


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_creates_five_sub_modules(self) -> None:
        from fleet_rlm.runtime.models.builders import RecursiveWorkspaceModule

        create_calls: list[dict[str, Any]] = []

        def _track_create(**kwargs: Any) -> MagicMock:
            create_calls.append(kwargs)
            return MagicMock(spec=dspy.Module)

        with patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm",
            side_effect=_track_create,
        ):
            RecursiveWorkspaceModule(
                interpreter=_mock_interpreter(),
                max_iterations=5,
                max_llm_calls=10,
            )

        assert len(create_calls) == 5
        sig_names = {call["signature"].__name__ for call in create_calls}
        assert sig_names == {
            "AssembleRecursiveWorkspaceContext",
            "PlanRecursiveSubqueries",
            "VerifyRecursiveAggregation",
            "ReflectAndReviseWorkspaceStep",
            "PlanRecursiveRepair",
        }

    def test_default_parameters(self) -> None:
        module = _build_module()
        assert module.max_passes == 3
        assert module.max_repair_attempts == 2
        assert module.subquery_budget == 4
        assert module.context_budget_chars == 32_000

    def test_custom_parameters(self) -> None:
        module = _build_module(max_passes=5, max_repair_attempts=1, subquery_budget=8)
        assert module.max_passes == 5
        assert module.max_repair_attempts == 1
        assert module.subquery_budget == 8


# ---------------------------------------------------------------------------
# Orchestration loop
# ---------------------------------------------------------------------------


class TestForwardLoop:
    def test_sufficient_on_first_pass_returns_immediately(self) -> None:
        module = _build_module(max_passes=3)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="context",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="sufficient",
                verified_summary="The answer is 42.",
                missing_evidence=[],
                contradictions=[],
                verification_rationale="",
            )
        )
        module._reflector = MagicMock()

        with (
            patch.object(module, "_execute_subqueries", return_value=["child answer"]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="test question")

        assert result.answer == "The answer is 42."
        assert result.passes == 1
        assert result.status == "sufficient"
        module._reflector.assert_not_called()

    def test_child_failure_prevents_false_sufficient_result(self) -> None:
        module = _build_module(max_passes=1, max_repair_attempts=0)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="context with enough detail",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="sufficient",
                verified_summary="looks good",
                missing_evidence=[],
                contradictions=[],
                verification_rationale="",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="finalize",
                revised_plan="done",
                rationale="ok",
                confidence=0.9,
            )
        )
        child_error = json.dumps(
            {
                "status": "error",
                "reason": "child_error",
                "error": "child failed",
            }
        )

        with (
            patch.object(module, "_execute_subqueries", return_value=[child_error]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="test question")

        assert result.status == "needs_human_review"
        module._reflector.assert_called_once()

    def test_needs_human_review_cannot_be_finalized_by_reflection(self) -> None:
        module = _build_module(max_passes=1, max_repair_attempts=0)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="context with enough detail",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="needs_human_review",
                verified_summary=(
                    "A candidate exists but verification is blocked by sandbox limitations."
                ),
                missing_evidence=["Independent verification is required."],
                contradictions=[],
                verification_rationale="Unable to create sandboxes for verification.",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="finalize",
                revised_plan="done",
                rationale="looks plausible",
                confidence=0.9,
            )
        )

        with (
            patch.object(module, "_execute_subqueries", return_value=["solution = []"]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="test question")

        assert result.status == "needs_human_review"
        assert "verification is blocked" in result.answer.lower()

    def test_adapter_failure_forces_repair_before_accepting_verifier(self) -> None:
        module = _build_module(max_passes=1, max_repair_attempts=1)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="context with enough detail",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="sufficient",
                verified_summary="looks good",
                missing_evidence=[],
                contradictions=[],
                verification_rationale="",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="finalize",
                revised_plan="done",
                rationale="ok",
                confidence=0.9,
            )
        )
        module._repairer = MagicMock(
            return_value=_mock_prediction(
                repair_mode="targeted_repair",
                repair_target="adapter failure",
                repair_steps=["retry failed child"],
                repair_subqueries=["retry q1"],
                repair_rationale="child failed",
            )
        )

        adapter_error = (
            "AdapterParseError: LM response cannot be serialized to a JSON object. "
            "Expected to find output fields in the LM response."
        )
        with (
            patch.object(module, "_execute_subqueries", return_value=[adapter_error]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="test question")

        assert result.status == "budget_exhausted"
        module._repairer.assert_called_once()
        repair_kwargs = module._repairer.call_args.kwargs
        assert "adapterparseerror" in repair_kwargs["latest_failure_signals"]

    def test_budget_exhaustion_after_max_passes(self) -> None:
        module = _build_module(max_passes=2)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="ctx",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="needs_more_recursion",
                verified_summary="partial",
                missing_evidence=["gap"],
                contradictions=[],
                verification_rationale="incomplete",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="recurse",
                revised_plan="try again",
                rationale="need more data",
                confidence=0.5,
            )
        )

        with (
            patch.object(
                module, "_execute_subqueries", return_value=["partial answer"]
            ),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="hard question")

        assert result.passes == 2
        assert result.status == "budget_exhausted"
        assert module._assembler.call_count == 2

    def test_finalize_action_exits_loop(self) -> None:
        module = _build_module(max_passes=5)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="ctx",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q"],
                decomposition_mode="single_pass",
                aggregation_plan="merge",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="needs_repair",
                verified_summary="good enough",
                missing_evidence=[],
                contradictions=[],
                verification_rationale="",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="finalize",
                revised_plan="done",
                rationale="sufficient",
                confidence=0.9,
            )
        )

        with (
            patch.object(module, "_execute_subqueries", return_value=["result"]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="q")

        assert result.status == "finalized"
        assert result.passes == 1
        assert module._assembler.call_count == 1

    def test_repair_path_triggers_reexecution(self) -> None:
        module = _build_module(max_passes=3, max_repair_attempts=2)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="ctx",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="merge",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="needs_repair",
                verified_summary="broken",
                missing_evidence=["key fact"],
                contradictions=["A vs B"],
                verification_rationale="",
            )
        )

        call_count = 0

        def _reflector_side_effect(**kwargs: Any) -> dspy.Prediction:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_prediction(
                    next_action="repair_and_retry",
                    revised_plan="fix it",
                    rationale="need repair",
                    confidence=0.6,
                )
            return _mock_prediction(
                next_action="finalize",
                revised_plan="done after repair",
                rationale="ok now",
                confidence=0.9,
            )

        module._reflector = MagicMock(side_effect=_reflector_side_effect)
        module._repairer = MagicMock(
            return_value=_mock_prediction(
                repair_mode="targeted_repair",
                repair_target="missing fact",
                repair_steps=["step1"],
                repair_subqueries=["fix query"],
                repair_rationale="targeted fix",
            )
        )

        execute_calls: list[list[str]] = []

        def _track_execute(subqueries: list[str], context: str, mode: str) -> list[str]:
            execute_calls.append(subqueries)
            return [f"answer for {subqueries[0]}"]

        with (
            patch.object(module, "_execute_subqueries", side_effect=_track_execute),
            patch.object(module, "_store_pass_evidence"),
        ):
            module(user_request="complex task")

        module._repairer.assert_called_once()
        assert len(execute_calls) >= 2
        assert any("fix query" in q for qs in execute_calls for q in qs)

    def test_human_review_action_returns_with_missing(self) -> None:
        module = _build_module(max_passes=3)

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="ctx",
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q"],
                decomposition_mode="single_pass",
                aggregation_plan="merge",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="needs_human_review",
                verified_summary="uncertain",
                missing_evidence=["critical gap"],
                contradictions=[],
                verification_rationale="",
            )
        )
        module._reflector = MagicMock(
            return_value=_mock_prediction(
                next_action="request_human_review",
                revised_plan="escalate",
                rationale="too uncertain",
                confidence=0.3,
            )
        )

        with (
            patch.object(module, "_execute_subqueries", return_value=["partial"]),
            patch.object(module, "_store_pass_evidence"),
        ):
            result = module(user_request="ambiguous task")

        assert result.status == "needs_human_review"
        assert result.missing == ["critical gap"]


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_loop_state_summary_format(self) -> None:
        module = _build_module(max_passes=3, max_repair_attempts=2, subquery_budget=4)
        summary = module._loop_state_summary(0, 0)
        assert "pass=1/3" in summary
        assert "repairs=0/2" in summary
        assert "subquery_budget=4" in summary

    def test_execute_subqueries_serial(self) -> None:
        module = _build_module()

        with patch(
            "fleet_rlm.runtime.tools.rlm_delegate.delegate_to_rlm",
            return_value={"status": "ok", "answer": "result"},
        ):
            outputs = module._execute_subqueries(["q1", "q2"], "ctx", "single_pass")

        assert len(outputs) == 2
        assert all(o == "result" for o in outputs)

    def test_execute_subqueries_fan_out(self) -> None:
        module = _build_module()

        with patch(
            "fleet_rlm.runtime.tools.rlm_delegate.delegate_to_rlm_batched",
            return_value={
                "status": "ok",
                "results": [
                    {"answer": "a1"},
                    {"answer": "a2"},
                ],
            },
        ):
            outputs = module._execute_subqueries(["q1", "q2"], "ctx", "fan_out")

        assert outputs == ["a1", "a2"]

    def test_execute_subqueries_fan_out_preserves_structured_errors(self) -> None:
        module = _build_module()

        with patch(
            "fleet_rlm.runtime.tools.rlm_delegate.delegate_to_rlm_batched",
            return_value={
                "status": "error",
                "results": [{"query": "good", "answer": "ok"}],
                "errors": [
                    {
                        "index": 1,
                        "query": "bad",
                        "reason": "child_error",
                        "error": "child failed",
                    }
                ],
            },
        ):
            outputs = module._execute_subqueries(["good", "bad"], "ctx", "fan_out")

        assert len(outputs) == 1
        payload = json.loads(outputs[0])
        assert payload == {
            "status": "error",
            "results": [{"query": "good", "answer": "ok"}],
            "errors": [
                {
                    "index": 1,
                    "query": "bad",
                    "reason": "child_error",
                    "error": "child failed",
                }
            ],
        }
        assert module._classify_subquery_failures(outputs) == [
            "output[0]:child_error",
            "output[0]:reason=child_error",
            "output[0]:status=error",
        ]

    def test_tool_error_output_is_classified_as_failure(self) -> None:
        module = _build_module()

        outputs = [
            json.dumps(
                {
                    "status": "error",
                    "reason": "tool_error",
                    "tool_name": "store_evidence",
                    "error": "Failed to inject tool 'store_evidence'",
                }
            )
        ]

        assert module._classify_subquery_failures(outputs) == [
            "output[0]:failed to inject tool",
            "output[0]:reason=tool_error",
            "output[0]:status=error",
            "output[0]:tool_error",
        ]

    def test_fetch_memory_catalog_empty_when_no_repo(self) -> None:
        module = _build_module()
        catalog = module._fetch_memory_catalog()
        assert isinstance(catalog, list)

    def test_store_pass_evidence_calls_injected_sink(self) -> None:
        sink_calls: list[dict[str, Any]] = []

        class _FakeSink:
            def store(self, **kwargs: Any) -> dict[str, Any]:
                sink_calls.append(kwargs)
                return {"status": "ok", "id": "x"}

            def list_items(self, **kwargs: Any) -> dict[str, Any]:
                return {"items": []}

        module = _build_module(evidence_sink=_FakeSink())
        module._store_pass_evidence(0, ["output1", "output2"])

        assert len(sink_calls) == 2
        assert sink_calls[0]["key"] == "pass_0_output_0"
        assert sink_calls[0]["kind"] == "context"
        assert "orchestrator" in sink_calls[0]["tags"]

    def test_store_pass_evidence_noops_when_no_sink(self) -> None:
        # Regression: the None-sink branch preserves the pre-refactor silent-skip
        # behavior when no host repository is attached to the interpreter.
        module = _build_module()
        module._store_pass_evidence(0, ["output1", "output2"])  # must not raise

    def test_fetch_memory_catalog_uses_injected_sink(self) -> None:
        class _FakeSink:
            def store(self, **kwargs: Any) -> dict[str, Any]:
                return {"status": "ok"}

            def list_items(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "items": [
                        {"scope_id": "s1", "kind": "context"},
                        {"scope_id": "s2", "kind": "observation"},
                    ]
                }

        module = _build_module(evidence_sink=_FakeSink())
        catalog = module._fetch_memory_catalog()
        assert catalog == ["s1:context", "s2:observation"]

    def test_assembled_context_fallback_when_assembler_returns_empty(self) -> None:
        # Regression: when ChatAdapter.parse errors cause the assembler's REPL loop
        # to fail and it returns a thin context (e.g. "solution = []"), child RLMs
        # receive no puzzle data and produce "Unable to determine..." answers.
        # The fix: forward() substitutes user_request verbatim when assembled_context
        # is shorter than 64 chars and fits within context_budget_chars.
        module = _build_module(max_passes=1, context_budget_chars=32_000)

        user_request = "Blocks World puzzle: initial=[[1,2],[3]], goal=[[3,2,1],[]]"
        captured_contexts: list[str] = []

        module._assembler = MagicMock(
            return_value=_mock_prediction(
                assembled_context_summary="solution = []",  # thin/failed assembler output
                selected_memory_handles=[],
                selected_evidence_ids=[],
                omission_rationale="parse error",
            )
        )
        module._planner = MagicMock(
            return_value=_mock_prediction(
                subqueries=["q1"],
                decomposition_mode="single_pass",
                aggregation_plan="concat",
                batching_strategy="serial",
                decomposition_rationale="",
            )
        )
        module._verifier = MagicMock(
            return_value=_mock_prediction(
                verification_status="sufficient",
                verified_summary="done",
                missing_evidence=[],
                contradictions=[],
                verification_rationale="",
            )
        )

        def _capture_subqueries(
            subqueries: list[str], context: str, mode: str
        ) -> list[str]:
            captured_contexts.append(context)
            return ["child answer"]

        with (
            patch.object(
                module, "_execute_subqueries", side_effect=_capture_subqueries
            ),
            patch.object(module, "_store_pass_evidence"),
        ):
            module(user_request=user_request)

        assert captured_contexts[0] == user_request, (
            "Expected fallback to user_request when assembled_context is too thin, "
            f"got: {captured_contexts[0]!r}"
        )


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    def test_recursive_workspace_in_discover_tools(self) -> None:
        from fleet_rlm.runtime.tools import discover_tools

        tools = discover_tools()
        names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
        assert "recursive_workspace" in names

    def test_recursive_workspace_stub_raises_without_runtime(self) -> None:
        from fleet_rlm.runtime.tools.sandbox_tools import recursive_workspace

        with pytest.raises(RuntimeError, match="AgentRuntime"):
            recursive_workspace(query="test")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_recursive_workspace_in_module_registry(self) -> None:
        from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

        assert "recursive_workspace" in RUNTIME_MODULE_REGISTRY
        defn = RUNTIME_MODULE_REGISTRY["recursive_workspace"]
        assert defn.class_name == "RecursiveWorkspaceModule"
        assert defn.module_class is not None


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


class TestCommand:
    def test_recursive_workspace_in_command_dispatch(self) -> None:
        from fleet_rlm.runtime.agent.commands import COMMAND_DISPATCH

        assert "recursive_workspace" in COMMAND_DISPATCH
        tool_name, required, optional = COMMAND_DISPATCH["recursive_workspace"]
        assert tool_name == "recursive_workspace"
        assert "query" in required
        assert "context" in optional
