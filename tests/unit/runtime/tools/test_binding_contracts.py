"""VAL-RLM contract tests for tool binding and variable-mode modules.

Covers:
- VAL-RLM-021: Variable-mode DSPy modules preserve signatures while keeping
               large inputs in REPL variables
- VAL-RLM-022: Host ReAct delegation tools are bound to the active Daytona interpreter
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest

# ---------------------------------------------------------------------------
# VAL-RLM-021: Variable-mode modules preserve signatures
# ---------------------------------------------------------------------------


class TestVariableModeModulePreservesSignatures:
    """VAL-RLM-021: Variable-mode modules keep signature fields, expose sub_rlm tools,
    and only inject metadata/previews for large inputs (not full content)."""

    def test_variable_mode_module_preserves_original_signature_fields(self) -> None:
        """RLMVariableExecutionModule preserves the caller's signature fields."""
        from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = MagicMock(spec=[])

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(
                signature=SummarizeLongDocument,
                interpreter=interp,
            )
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["signature"] is SummarizeLongDocument, (
                "Original signature must be preserved, not replaced with a generic one"
            )

    def test_variable_mode_default_signature_is_rlm_variable_signature(self) -> None:
        """Default signature is RLMVariableSignature (task + prompt → answer)."""
        from fleet_rlm.runtime.agent.signatures import RLMVariableSignature
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = MagicMock(spec=[])

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp)
            assert mock_create.call_args[1]["signature"] is RLMVariableSignature

    def test_variable_mode_exposes_sub_rlm_tools_when_interpreter_has_them(self) -> None:
        """sub_rlm and sub_rlm_batched from the interpreter are passed as tools."""
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = MagicMock()
        interp.sub_rlm = MagicMock(name="sub_rlm")
        interp.sub_rlm_batched = MagicMock(name="sub_rlm_batched")

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp)
            call_kwargs = mock_create.call_args[1]
            tools = call_kwargs.get("tools", [])
            assert interp.sub_rlm in tools, "sub_rlm must be in tools"
            assert interp.sub_rlm_batched in tools, "sub_rlm_batched must be in tools"

    def test_variable_mode_no_sub_rlm_tools_when_interpreter_lacks_them(self) -> None:
        """Tools list is None/empty when interpreter has no sub_rlm."""
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = MagicMock(spec=[])  # No sub_rlm attributes

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp)
            call_kwargs = mock_create.call_args[1]
            tools = call_kwargs.get("tools")
            assert tools is None or tools == [], f"No tools should be passed for interpreter without sub_rlm: {tools}"

    def test_variable_mode_max_output_chars_is_smaller_than_default(self) -> None:
        """Variable-mode uses tighter max_output_chars to force REPL variable usage."""
        from fleet_rlm.runtime.modules.variable_mode import (
            VARIABLE_MODE_MAX_OUTPUT_CHARS,
            RLMVariableExecutionModule,
        )

        interp = MagicMock(spec=[])

        with patch("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm") as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp)
            call_kwargs = mock_create.call_args[1]
            max_output = call_kwargs.get("max_output_chars")
            assert max_output is not None
            assert max_output == VARIABLE_MODE_MAX_OUTPUT_CHARS
            # Variable mode must use significantly fewer chars than default to force REPL variable usage
            assert max_output <= 10_000, (
                f"Variable mode max_output_chars {max_output} is too large — large inputs should stay as REPL variables"
            )

    def test_variable_mode_forward_passes_kwargs_to_inner_rlm(self) -> None:
        """forward(**kwargs) delegates to the inner RLM with the same kwargs."""
        from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
        from fleet_rlm.runtime.modules.variable_mode import RLMVariableExecutionModule

        interp = MagicMock(spec=[])
        mock_rlm = MagicMock()
        mock_rlm.return_value = dspy.Prediction(
            summary="Summary here",
            key_points=["point 1"],
            coverage_pct=90,
        )

        with patch(
            "fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm",
            return_value=mock_rlm,
        ):
            module = RLMVariableExecutionModule(
                signature=SummarizeLongDocument,
                interpreter=interp,
            )
            result = module(document="long doc text", focus="performance")

        mock_rlm.assert_called_once_with(document="long doc text", focus="performance")
        assert result.summary == "Summary here"

    def test_variable_mode_registry_entries_are_marked_correctly(self) -> None:
        """Registry entries for variable-mode modules have variable_mode=True."""
        from fleet_rlm.runtime.modules.registry import RUNTIME_MODULE_REGISTRY

        variable_mode_entries = {name: defn for name, defn in RUNTIME_MODULE_REGISTRY.items() if defn.variable_mode}
        assert len(variable_mode_entries) > 0, "At least one variable-mode module must exist"
        # Spot check: summarize_long_document should be variable mode
        assert "summarize_long_document" in variable_mode_entries
        assert "extract_from_logs" in variable_mode_entries


# ---------------------------------------------------------------------------
# VAL-RLM-022: Host ReAct delegation tools bound to active Daytona interpreter
# ---------------------------------------------------------------------------


class TestDelegationToolsBoundToInterpreter:
    """VAL-RLM-022: delegate_to_rlm receives active interpreter via binding closure."""

    def test_bound_delegate_tool_uses_active_interpreter(self) -> None:
        """bind_runtime_tools wraps delegate_to_rlm with the active interpreter in closure."""
        import fleet_rlm.runtime.tools.binding as binding_mod
        from fleet_rlm.runtime.tools.binding import bind_runtime_tools
        from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

        captured_interpreters: list[Any] = []

        def _mock_delegate(
            *,
            query: str,
            context: str,
            document_url: Any,
            interpreter: Any,
        ) -> dict[str, Any]:
            captured_interpreters.append(interpreter)
            return {"status": "ok", "answer": "bound answer"}

        class _FakeRuntime:
            core_memory: dict = {}

        fake_interp = object()
        runtime = _FakeRuntime()

        tools = [delegate_to_rlm]
        bound_tools = bind_runtime_tools(tools, runtime=runtime, interpreter=fake_interp)

        # Find the bound delegate_to_rlm tool
        bound_delegate = None
        for tool in bound_tools:
            name = getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "")
            if name == "delegate_to_rlm":
                bound_delegate = tool
                break

        assert bound_delegate is not None, "delegate_to_rlm must appear in bound tools"

        # The binding module imports delegate_to_rlm as _delegate_to_rlm
        with patch.object(binding_mod, "_delegate_to_rlm", _mock_delegate):
            func = getattr(bound_delegate, "func", bound_delegate)
            func(query="test", context="", document_url="")

        assert len(captured_interpreters) == 1
        assert captured_interpreters[0] is fake_interp, "Bound tool must use the interpreter from the binding closure"

    def test_bound_delegate_tool_ignores_client_supplied_interpreter(self) -> None:
        """Bound delegate_to_rlm signature has no interpreter parameter for callers."""
        import inspect

        from fleet_rlm.runtime.tools.binding import bind_runtime_tools
        from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

        class _FakeRuntime:
            core_memory: dict = {}

        fake_interp = object()
        tools = [delegate_to_rlm]
        bound_tools = bind_runtime_tools(tools, runtime=_FakeRuntime(), interpreter=fake_interp)

        for tool in bound_tools:
            name = getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "")
            if name != "delegate_to_rlm":
                continue
            func = getattr(tool, "func", tool)
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            assert "interpreter" not in params, (
                f"Bound delegate_to_rlm must not expose 'interpreter' as a callable param: {params}"
            )

    def test_batched_delegate_tool_also_bound_to_interpreter(self) -> None:
        """delegate_to_rlm_batched is also bound to the active interpreter."""
        import fleet_rlm.runtime.tools.binding as binding_mod
        from fleet_rlm.runtime.tools.binding import bind_runtime_tools
        from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm_batched

        captured: list[Any] = []

        def _mock_batched(
            *,
            queries: list,
            context: str,
            document_url: Any,
            interpreter: Any,
        ) -> dict[str, Any]:
            captured.append(interpreter)
            return {"status": "ok", "results": []}

        class _FakeRuntime:
            core_memory: dict = {}

        fake_interp = object()
        tools = [delegate_to_rlm_batched]
        bound_tools = bind_runtime_tools(tools, runtime=_FakeRuntime(), interpreter=fake_interp)

        bound_batched = None
        for tool in bound_tools:
            name = getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "")
            if name == "delegate_to_rlm_batched":
                bound_batched = tool
                break

        assert bound_batched is not None

        # The binding module imports delegate_to_rlm_batched as _delegate_to_rlm_batched
        with patch.object(binding_mod, "_delegate_to_rlm_batched", _mock_batched):
            func = getattr(bound_batched, "func", bound_batched)
            func(queries=["a", "b"], context="", document_url="")

        assert len(captured) == 1
        assert captured[0] is fake_interp

    def test_agent_runtime_wires_interpreter_to_delegation_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AgentRuntime(interpreter=X) results in delegate_to_rlm using interpreter X."""
        from fleet_rlm.runtime.agent.runtime import AgentRuntime
        from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm, delegate_to_rlm_batched

        # Patch dspy.ReAct to avoid real LLM construction
        class _FakeReAct:
            def __init__(self, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", _FakeReAct)
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [delegate_to_rlm, delegate_to_rlm_batched],
        )

        fake_interp = MagicMock()
        fake_interp._remaining_llm_budget.return_value = 50
        fake_interp.verbose = False

        captured_interp: list[Any] = []

        def _mock_delegate(*, query: str, context: str, document_url: Any, interpreter: Any) -> dict:
            captured_interp.append(interpreter)
            return {"status": "ok", "answer": "wired"}

        rt = AgentRuntime(interpreter=fake_interp)

        # Find the bound delegate tool in rt.tools and call it
        bound_delegate = None
        for tool in rt.tools:
            name = getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "")
            if name == "delegate_to_rlm":
                bound_delegate = tool
                break

        assert bound_delegate is not None, "delegate_to_rlm must be in AgentRuntime tools"

        import fleet_rlm.runtime.tools.binding as binding_mod

        with patch.object(binding_mod, "_delegate_to_rlm", _mock_delegate):
            func = getattr(bound_delegate, "func", bound_delegate)
            func(query="test task", context="")

        assert len(captured_interp) == 1
        assert captured_interp[0] is fake_interp, "AgentRuntime must wire the active interpreter to delegate_to_rlm"

    def test_interpreter_absent_removes_delegation_tools_from_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AgentRuntime without interpreter omits interpreter-bound tools."""
        from fleet_rlm.runtime.agent.runtime import AgentRuntime
        from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

        class _FakeReAct:
            def __init__(self, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", _FakeReAct)
        monkeypatch.setattr(
            "fleet_rlm.runtime.agent.runtime.discover_tools",
            lambda: [delegate_to_rlm],
        )

        rt = AgentRuntime(interpreter=None)

        tool_names = {
            getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", "") for tool in rt.tools
        }
        assert "delegate_to_rlm" not in tool_names, (
            "delegate_to_rlm must be removed from tools when no interpreter is provided"
        )
