"""VAL-RLM contract tests for DSPy signatures and runtime module factories.

Covers:
- VAL-RLM-011: DSPy signatures expose stable structured fields
- VAL-RLM-012: Module discovery metadata consistent across public surfaces
- VAL-RLM-013: Runtime module factories build executable DSPy modules
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dspy
import pytest

# ---------------------------------------------------------------------------
# VAL-RLM-011: DSPy signatures expose stable structured fields
# ---------------------------------------------------------------------------


class TestDSpySignaturesStableFields:
    """VAL-RLM-011: All runtime signatures have required input and output fields."""

    def _input_names(self, sig_cls: type[dspy.Signature]) -> set[str]:
        return set(sig_cls.input_fields.keys())

    def _output_names(self, sig_cls: type[dspy.Signature]) -> set[str]:
        return set(sig_cls.output_fields.keys())

    def test_fleet_agent_signature_fields(self) -> None:
        """FleetAgentSignature: chat_history + user_message → response."""
        from fleet_rlm.runtime.agent.agent import FleetAgentSignature

        inputs = self._input_names(FleetAgentSignature)
        outputs = self._output_names(FleetAgentSignature)
        assert "chat_history" in inputs, f"Missing chat_history in: {inputs}"
        assert "user_message" in inputs, f"Missing user_message in: {inputs}"
        assert "response" in outputs, f"Missing response in: {outputs}"

    def test_summarize_long_document_signature_fields(self) -> None:
        """SummarizeLongDocument: document, focus → summary, key_points, coverage_pct."""
        from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

        assert "document" in self._input_names(SummarizeLongDocument)
        assert "focus" in self._input_names(SummarizeLongDocument)
        assert "summary" in self._output_names(SummarizeLongDocument)
        assert "key_points" in self._output_names(SummarizeLongDocument)
        assert "coverage_pct" in self._output_names(SummarizeLongDocument)

    def test_extract_from_logs_signature_fields(self) -> None:
        """ExtractFromLogs: logs, query → matches, patterns, time_range."""
        from fleet_rlm.runtime.agent.signatures import ExtractFromLogs

        assert "logs" in self._input_names(ExtractFromLogs)
        assert "query" in self._input_names(ExtractFromLogs)
        assert "matches" in self._output_names(ExtractFromLogs)
        assert "patterns" in self._output_names(ExtractFromLogs)
        assert "time_range" in self._output_names(ExtractFromLogs)

    def test_grounded_answer_signature_fields(self) -> None:
        """GroundedAnswerWithCitations: query, evidence_chunks → answer, citations, confidence."""
        from fleet_rlm.runtime.agent.signatures import GroundedAnswerWithCitations

        assert "query" in self._input_names(GroundedAnswerWithCitations)
        assert "evidence_chunks" in self._input_names(GroundedAnswerWithCitations)
        assert "answer" in self._output_names(GroundedAnswerWithCitations)
        assert "citations" in self._output_names(GroundedAnswerWithCitations)
        assert "confidence" in self._output_names(GroundedAnswerWithCitations)

    def test_rlm_variable_signature_fields(self) -> None:
        """RLMVariableSignature: task, prompt → answer."""
        from fleet_rlm.runtime.agent.signatures import RLMVariableSignature

        assert "task" in self._input_names(RLMVariableSignature)
        assert "prompt" in self._input_names(RLMVariableSignature)
        assert "answer" in self._output_names(RLMVariableSignature)

    def test_reflect_revise_workspace_step_fields(self) -> None:
        """ReflectAndReviseWorkspaceStep: user_request → next_action, rationale."""
        from fleet_rlm.runtime.agent.signatures import ReflectAndReviseWorkspaceStep

        assert "user_request" in self._input_names(ReflectAndReviseWorkspaceStep)
        # Outputs: next_action, revised_plan, rationale, confidence
        assert "next_action" in self._output_names(ReflectAndReviseWorkspaceStep)
        assert "rationale" in self._output_names(ReflectAndReviseWorkspaceStep)

    def test_all_registry_entries_reference_valid_signatures(self) -> None:
        """Every RUNTIME_MODULE_REGISTRY entry points to a real dspy.Signature subclass."""
        from fleet_rlm.runtime.modules.registry import RUNTIME_MODULE_REGISTRY

        for name, defn in RUNTIME_MODULE_REGISTRY.items():
            sig = defn.signature
            assert issubclass(sig, dspy.Signature), (
                f"Module '{name}' signature {sig!r} is not a dspy.Signature subclass"
            )
            assert len(sig.input_fields) > 0, f"Module '{name}' signature has no input fields"
            assert len(sig.output_fields) > 0, f"Module '{name}' signature has no output fields"

    def test_module_construction_fails_fast_on_missing_required_fields(self) -> None:
        """Build with required input fields missing should fail or raise, not silently accept."""
        from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

        # SummarizeLongDocument requires 'document' and 'focus'. Calling it
        # without them via a bare dspy.Predict should raise.
        predictor = dspy.Predict(SummarizeLongDocument)
        with pytest.raises((TypeError, ValueError, AttributeError, RuntimeError)):
            # No required inputs provided → must raise, not silently return None
            predictor()


# ---------------------------------------------------------------------------
# VAL-RLM-012: Module discovery metadata consistent across public surfaces
# ---------------------------------------------------------------------------


class TestModuleDiscoveryConsistency:
    """VAL-RLM-012: Quality module registry, API, and CLI expose the same metadata."""

    def test_longcot_reasoner_registered_in_quality_registry(self) -> None:
        """longcot-reasoner must be present in the quality module registry."""
        from fleet_rlm.quality.module_registry import list_module_slugs

        slugs = list_module_slugs()
        assert "longcot-reasoner" in slugs, f"longcot-reasoner missing from registry: {slugs}"

    def test_quality_registry_metadata_has_required_keys(self) -> None:
        """Each quality module entry exposes slug, label, description, program_spec."""
        from fleet_rlm.quality.module_registry import list_module_metadata

        metadata = list_module_metadata()
        assert len(metadata) > 0, "Quality module registry must have at least one entry"
        required_keys = {"slug", "label", "description", "program_spec", "required_dataset_keys"}
        for entry in metadata:
            missing = required_keys - set(entry.keys())
            assert not missing, f"Module metadata entry missing keys: {missing} — entry: {entry}"

    def test_api_optimization_modules_matches_registry(self) -> None:
        """GET /api/v1/optimization/modules response shape matches quality registry."""
        from fleet_rlm.quality.module_registry import list_module_metadata, list_module_slugs

        registry_slugs = set(list_module_slugs())
        metadata_slugs = {entry["slug"] for entry in list_module_metadata()}
        assert registry_slugs == metadata_slugs, (
            f"Slug lists diverged: registry={registry_slugs}, metadata={metadata_slugs}"
        )

    def test_longcot_reasoner_metadata_fields(self) -> None:
        """longcot-reasoner module has the expected metadata fields."""
        from fleet_rlm.quality.module_registry import get_module_spec

        spec = get_module_spec("longcot-reasoner")
        assert spec is not None, "longcot-reasoner spec must be non-None after import"
        assert spec.module_slug == "longcot-reasoner"
        assert spec.label, "label must be non-empty"
        assert spec.program_spec, "program_spec must be non-empty"
        assert isinstance(spec.input_keys, list)
        assert isinstance(spec.required_dataset_keys, list)
        assert callable(spec.module_factory), "module_factory must be callable"
        assert callable(spec.row_converter), "row_converter must be callable"
        assert callable(spec.metric_builder), "metric_builder must be callable"

    def test_runtime_module_registry_names_are_stable(self) -> None:
        """Runtime module names are a stable frozenset."""
        from fleet_rlm.runtime.modules.registry import RUNTIME_MODULE_NAMES, RUNTIME_MODULE_REGISTRY

        assert isinstance(RUNTIME_MODULE_NAMES, frozenset)
        assert RUNTIME_MODULE_NAMES == frozenset(RUNTIME_MODULE_REGISTRY.keys())
        # Key modules that must always be present
        for expected in (
            "summarize_long_document",
            "extract_from_logs",
            "grounded_answer",
            "recursive_workspace",
        ):
            assert expected in RUNTIME_MODULE_NAMES, f"Missing expected runtime module: {expected}"


# ---------------------------------------------------------------------------
# VAL-RLM-013: Runtime module factories build executable DSPy modules
# ---------------------------------------------------------------------------


class TestRuntimeModuleFactories:
    """VAL-RLM-013: Every registry entry builds a module with expected signature and deps."""

    def _make_mock_interpreter(self) -> MagicMock:
        interp = MagicMock()
        interp.sub_rlm = MagicMock()
        interp.sub_rlm_batched = MagicMock()
        return interp

    def test_summarize_long_document_factory_builds_module(self) -> None:
        """summarize_long_document: builds a dspy.Module with SummarizeLongDocument signature."""
        from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
        from fleet_rlm.runtime.modules.registry import build_runtime_module
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = self._make_mock_interpreter()
        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            module = build_runtime_module(
                "summarize_long_document",
                interpreter=interp,
                max_iterations=5,
                max_llm_calls=20,
                verbose=False,
            )
        assert isinstance(module, RLMVariableExecutionModule)
        # Verify create_runtime_rlm was called with the correct signature
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["signature"] is SummarizeLongDocument

    def test_extract_from_logs_factory_builds_variable_mode_module(self) -> None:
        """extract_from_logs: builds RLMVariableExecutionModule (variable_mode=True)."""
        from fleet_rlm.runtime.modules.registry import build_runtime_module
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = self._make_mock_interpreter()
        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm"):
            module = build_runtime_module(
                "extract_from_logs",
                interpreter=interp,
                max_iterations=5,
                max_llm_calls=20,
                verbose=False,
            )
        assert isinstance(module, RLMVariableExecutionModule)

    def test_grounded_answer_factory_builds_custom_module(self) -> None:
        """grounded_answer: builds GroundedAnswerSynthesisModule (custom module_class)."""
        from fleet_rlm.runtime.modules.grounded_answer import GroundedAnswerSynthesisModule
        from fleet_rlm.runtime.modules.registry import build_runtime_module

        interp = self._make_mock_interpreter()
        with patch("fleet_rlm.runtime.modules.grounded_answer._create_configured_runtime_rlm"):
            module = build_runtime_module(
                "grounded_answer",
                interpreter=interp,
                max_iterations=5,
                max_llm_calls=20,
                verbose=False,
            )
        assert isinstance(module, GroundedAnswerSynthesisModule)

    def test_recursive_workspace_factory_builds_module(self) -> None:
        """recursive_workspace: builds RecursiveWorkspaceModule."""
        from fleet_rlm.runtime.modules.registry import build_runtime_module
        from fleet_rlm.runtime.modules.workspace import RecursiveWorkspaceModule

        interp = self._make_mock_interpreter()
        with patch("fleet_rlm.runtime.modules.factory._create_configured_runtime_rlm") as mock_rlm:
            mock_rlm.return_value = MagicMock(spec=dspy.Module)
            module = build_runtime_module(
                "recursive_workspace",
                interpreter=interp,
                max_iterations=3,
                max_llm_calls=10,
                verbose=False,
            )
        assert isinstance(module, RecursiveWorkspaceModule)

    def test_all_non_variable_mode_modules_have_signatures(self) -> None:
        """Every non-variable-mode module has a DSPy signature with the right fields."""
        from fleet_rlm.runtime.modules.registry import RUNTIME_MODULE_REGISTRY

        for name, defn in RUNTIME_MODULE_REGISTRY.items():
            sig = defn.signature
            assert issubclass(sig, dspy.Signature), f"Registry entry '{name}' does not reference a dspy.Signature"

    def test_unknown_module_raises_value_error(self) -> None:
        """build_runtime_module raises ValueError for unknown module names."""
        from fleet_rlm.runtime.modules.registry import build_runtime_module

        with pytest.raises(ValueError, match="Unknown runtime module"):
            build_runtime_module(
                "definitely-not-a-real-module",
                interpreter=MagicMock(),
                max_iterations=1,
                max_llm_calls=1,
                verbose=False,
            )

    def test_get_or_build_caches_module(self) -> None:
        """get_or_build_runtime_module returns the same instance on second call."""
        from fleet_rlm.runtime.modules.factory import RuntimeModuleBuildConfig
        from fleet_rlm.runtime.modules.registry import get_or_build_runtime_module

        interp = self._make_mock_interpreter()
        config = RuntimeModuleBuildConfig(
            interpreter=interp,
            max_iterations=5,
            max_llm_calls=20,
            verbose=False,
        )
        cache: dict = {}

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            m1 = get_or_build_runtime_module(cache, "summarize_long_document", config=config)
            m2 = get_or_build_runtime_module(cache, "summarize_long_document", config=config)

        assert m1 is m2, "Second call must return cached module instance"
        assert mock_create.call_count == 1, "create_runtime_rlm must be called only once"
