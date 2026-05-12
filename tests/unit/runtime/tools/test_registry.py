"""Unit tests for discover_tools() registry.

Covers VAL-TOOLS-001 through VAL-TOOLS-011 from the validation contract.
"""

from __future__ import annotations

import types
from typing import Any

import pytest


def _tool_names(tools: list[Any]) -> set[str]:
    return {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in tools}


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# VAL-TOOLS-001: discover_tools() returns a list of callables
# VAL-TOOLS-010: Stable ordering across calls
# ---------------------------------------------------------------------------


def test_discover_tools_returns_nonempty_list_of_callables() -> None:
    """VAL-TOOLS-001: discover_tools() returns a non-empty list of callables."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()

    assert isinstance(tools, list)
    assert len(tools) > 0
    for tool in tools:
        assert callable(tool), f"Tool {tool!r} is not callable"


def test_tool_modules_are_explicitly_registered() -> None:
    """Tool discovery imports a deliberate module list, not every file in the package."""
    from fleet_rlm.runtime.tools.registry import TOOL_MODULE_NAMES

    assert TOOL_MODULE_NAMES == (
        "buffer_tools",
        "chunking_tools",
        "document_tools",
        "filesystem",
        "memory_tools",
        "rlm_delegate",
        "sandbox_filesystem",
        "sandbox_tools",
    )


def test_discover_tools_uses_explicit_module_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry imports only modules listed in TOOL_MODULE_NAMES."""
    import types

    from fleet_rlm.runtime.tools import registry
    from fleet_rlm.runtime.tools._marker import tool_fn

    imported: list[str] = []

    def _fake_import_module(module_name: str) -> Any:
        imported.append(module_name)
        module = types.ModuleType(module_name)
        tool_name = f"{module_name.rsplit('.', maxsplit=1)[-1]}_tool"

        def _explicit_tool() -> str:
            return tool_name

        _explicit_tool.__name__ = tool_name
        module.__dict__[tool_name] = tool_fn(_explicit_tool)
        return module

    monkeypatch.setattr(registry.importlib, "import_module", _fake_import_module)

    tools = registry.discover_tools()

    assert imported == [f"fleet_rlm.runtime.tools.{module_name}" for module_name in registry.TOOL_MODULE_NAMES]
    assert _tool_names(tools) == {f"{module_name}_tool" for module_name in registry.TOOL_MODULE_NAMES}


def test_discover_tools_stable_ordering() -> None:
    """VAL-TOOLS-010: Multiple calls return the same tool order."""
    from fleet_rlm.runtime.tools import discover_tools

    tools_first = discover_tools()
    tools_second = discover_tools()

    names_first = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools_first]
    names_second = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools_second]

    assert names_first == names_second
    assert names_first == sorted(names_first), "Tools should be returned in alphabetical order"


# ---------------------------------------------------------------------------
# VAL-TOOLS-002: Only @tool_fn-decorated functions are collected
# ---------------------------------------------------------------------------


def test_only_designated_functions_collected() -> None:
    """VAL-TOOLS-002: Helper functions without @tool_fn are ignored."""
    from fleet_rlm.runtime.tools import _collect_tools_from_modules

    # Module with a helper (no marker) and a designated tool (with marker)
    mod = types.ModuleType("test_mod")
    mod.__dict__["helper_fn"] = lambda x: x  # no marker
    mod.__dict__["build_tools"] = lambda agent: []  # no marker

    tools = _collect_tools_from_modules([mod])
    names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]

    assert "helper_fn" not in names
    assert "build_tools" not in names


def test_tool_fn_marked_functions_are_collected() -> None:
    """VAL-TOOLS-002: Functions with __is_fleet_tool__ = True are collected."""
    from fleet_rlm.runtime.tools import _collect_tools_from_modules
    from fleet_rlm.runtime.tools._marker import tool_fn

    @tool_fn
    def my_tool(query: str) -> dict[str, Any]:
        """A designated tool."""
        return {"result": query}

    mod = types.ModuleType("test_mod_b")
    mod.__dict__["my_tool"] = my_tool

    tools = _collect_tools_from_modules([mod])
    names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]

    assert "my_tool" in names


def test_private_names_skipped() -> None:
    """VAL-TOOLS-002: Names starting with _ are never collected."""
    from fleet_rlm.runtime.tools import _collect_tools_from_modules
    from fleet_rlm.runtime.tools._marker import tool_fn

    @tool_fn
    def _private_tool(x: str) -> str:
        """Private helper accidentally marked."""
        return x

    mod = types.ModuleType("test_private")
    mod.__dict__["_private_tool"] = _private_tool

    tools = _collect_tools_from_modules([mod])
    names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]

    assert "_private_tool" not in names


# ---------------------------------------------------------------------------
# VAL-TOOLS-003: Discovered tools are valid for dspy.ReAct
# ---------------------------------------------------------------------------


def test_discovered_tools_valid_for_react() -> None:
    """VAL-TOOLS-003: Discovered tools can be passed to dspy.ReAct without error."""
    import dspy

    from fleet_rlm.runtime.agent.agent import FleetAgentSignature
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    assert len(tools) > 0

    # dspy.ReAct construction must not raise
    react = dspy.ReAct(FleetAgentSignature, tools=tools, max_iters=1)
    assert react is not None


def test_high_risk_tool_descriptions_preserve_routing_guidance() -> None:
    """High-risk tool descriptions stay concise while keeping selection guidance."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = {getattr(tool, "name", getattr(tool, "__name__", "")): tool for tool in discover_tools()}

    assert _normalize_whitespace(tools["delegate_to_rlm"].desc or "") == (
        "Run a single child query in a Daytona RLM sandbox. For multiple independent tasks use "
        "delegate_to_rlm_batched. Pass document_url to auto-inject a remote document into the "
        "RLM context before execution."
    )
    assert _normalize_whitespace(tools["delegate_to_rlm_batched"].desc or "") == (
        "Fan out independent child RLM queries concurrently. Prefer over sequential "
        "delegate_to_rlm calls. Use execute_code with llm_query_batched() when work is already "
        "inside one Daytona sandbox."
    )
    assert _normalize_whitespace(tools["execute_code"].desc or "") == (
        "Execute Python code in the Daytona sandbox. Use llm_query_batched() inside the code for "
        "many lightweight semantic prompts, sub_rlm_batched() for recursive child tasks."
    )


# ---------------------------------------------------------------------------
# VAL-TOOLS-004: Sandbox execution tool present
# ---------------------------------------------------------------------------


def test_sandbox_execution_tool_present() -> None:
    """VAL-TOOLS-004: A sandbox code execution tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    sandbox_names = {n for n in names if "execute" in n or "code" in n}
    assert sandbox_names, f"Expected a sandbox execution tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-005: Filesystem tool present
# ---------------------------------------------------------------------------


def test_filesystem_tool_present() -> None:
    """VAL-TOOLS-005: A filesystem operations tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    fs_names = {n for n in names if "file" in n.lower()}
    assert fs_names, f"Expected a filesystem tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-006: Document loading tool present
# ---------------------------------------------------------------------------


def test_document_loading_tool_present() -> None:
    """VAL-TOOLS-006: A document loading tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    doc_names = {n for n in names if "document" in n.lower() or "load" in n}
    assert doc_names, f"Expected a document tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-007: Chunking tool present
# ---------------------------------------------------------------------------


def test_chunking_tool_present() -> None:
    """VAL-TOOLS-007: A chunking tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    chunk_names = {n for n in names if "chunk" in n.lower()}
    assert chunk_names, f"Expected a chunking tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-008: Buffer/volume operations tool present
# ---------------------------------------------------------------------------


def test_buffer_volume_tool_present() -> None:
    """VAL-TOOLS-008: A buffer/volume operations tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    buf_names = {n for n in names if "buffer" in n.lower() or "volume" in n.lower()}
    assert buf_names, f"Expected a buffer/volume tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-009: Core memory tool present
# ---------------------------------------------------------------------------


def test_core_memory_tool_present() -> None:
    """VAL-TOOLS-009: A core memory tool exists in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    mem_names = {n for n in names if "memory" in n.lower()}
    assert mem_names, f"Expected a core memory tool but found only: {sorted(names)}"


# ---------------------------------------------------------------------------
# VAL-TOOLS-011: Duplicate tool names raise ValueError
# ---------------------------------------------------------------------------


def test_duplicate_tool_names_raise_value_error() -> None:
    """VAL-TOOLS-011: _collect_tools_from_modules raises ValueError on duplicate names."""
    from fleet_rlm.runtime.tools import _collect_tools_from_modules
    from fleet_rlm.runtime.tools._marker import tool_fn

    @tool_fn
    def duplicate_tool(x: str) -> str:
        """First version."""
        return x

    @tool_fn
    def duplicate_tool(x: str) -> str:  # noqa: F811
        """Second version with the same name."""
        return x + "_2"

    mod_a = types.ModuleType("dupe_mod_a")
    mod_a.__dict__["duplicate_tool"] = duplicate_tool

    mod_b = types.ModuleType("dupe_mod_b")

    # Give mod_b a genuinely separate function with the same attribute name
    def _second_fn(x: str) -> str:  # pragma: no cover
        return x + "_second"

    _second_fn.__is_fleet_tool__ = True  # type: ignore[attr-defined]
    _second_fn.__name__ = "duplicate_tool"
    mod_b.__dict__["duplicate_tool"] = _second_fn

    with pytest.raises(ValueError, match="[Dd]uplicate"):
        _collect_tools_from_modules([mod_a, mod_b])


def test_no_duplicates_within_single_valid_module() -> None:
    """VAL-TOOLS-011: A module with unique tool names does not raise."""
    from fleet_rlm.runtime.tools import _collect_tools_from_modules
    from fleet_rlm.runtime.tools._marker import tool_fn

    @tool_fn
    def tool_alpha(x: str) -> str:
        """Tool alpha."""
        return x

    @tool_fn
    def tool_beta(y: int) -> int:
        """Tool beta."""
        return y

    mod = types.ModuleType("clean_mod")
    mod.__dict__["tool_alpha"] = tool_alpha
    mod.__dict__["tool_beta"] = tool_beta

    tools = _collect_tools_from_modules([mod])
    names = [getattr(t, "name", getattr(t, "__name__", "")) for t in tools]

    assert "tool_alpha" in names
    assert "tool_beta" in names
    assert len(names) == 2


# ---------------------------------------------------------------------------
# Registry completeness: all expected categories in one call
# ---------------------------------------------------------------------------


def test_all_expected_tool_categories_present() -> None:
    """All six expected tool categories appear in the registry simultaneously."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    # Check each category has at least one representative
    assert any("execute" in n or "code" in n for n in names), f"Missing sandbox execution tool. Found: {sorted(names)}"
    assert any("file" in n.lower() for n in names), f"Missing filesystem tool. Found: {sorted(names)}"
    assert any("document" in n.lower() or "load" in n for n in names), f"Missing document tool. Found: {sorted(names)}"
    assert any("chunk" in n.lower() for n in names), f"Missing chunking tool. Found: {sorted(names)}"
    assert any("buffer" in n.lower() or "volume" in n.lower() for n in names), (
        f"Missing buffer/volume tool. Found: {sorted(names)}"
    )
    assert any("memory" in n.lower() for n in names), f"Missing core memory tool. Found: {sorted(names)}"
