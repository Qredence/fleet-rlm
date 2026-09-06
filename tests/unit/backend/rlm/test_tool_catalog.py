"""Native RLM construction retains one closed, authority-aware tool namespace."""

from dataclasses import FrozenInstanceError

import dspy
import pytest

from fleet_rlm.rlm.program import FleetProgramSpec, FleetToolCatalog, FleetToolEntry, FleetToolKind, build_program
from fleet_rlm.rlm.result import RLMConfigError


def _tool(name):
    return dspy.Tool(lambda: "ok", name=name)


@pytest.mark.parametrize("name", ["llm_query", "llm_query_batched", "print", "SUBMIT", "not-valid", "class"])
def test_catalog_rejects_native_namespace_collisions(name):
    with pytest.raises(RLMConfigError):
        FleetToolCatalog.from_tools([_tool(name)])


def test_catalog_excludes_settlement_and_rejects_duplicate_names():
    catalog = FleetToolCatalog(
        (
            FleetToolEntry(_tool("read_data"), FleetToolKind.HOST_AUTHORIZED),
            FleetToolEntry(_tool("commit_turn"), FleetToolKind.SETTLEMENT_ONLY),
        )
    )
    assert [tool.name for tool in catalog.model_tools()] == ["read_data"]
    rlm = build_program(FleetProgramSpec(signature="question -> answer", tool_catalog=catalog))
    assert set(rlm.tools) == {"read_data"}
    with pytest.raises(RLMConfigError, match="duplicate"):
        FleetToolCatalog.from_tools([_tool("read_data"), _tool("read_data")])


def test_program_spec_snapshots_membership_and_builds_native_module():
    tools = [_tool("read_data")]
    spec = FleetProgramSpec(signature="question -> answer", tools=tools)
    tools.clear()
    assert spec.tools
    with pytest.raises(FrozenInstanceError):
        spec.verbose = False
    rlm = build_program(spec)
    assert type(rlm) is dspy.RLM
    assert set(rlm.tools) == {"read_data"}


def test_catalog_checks_final_constructed_namespace():
    catalog = FleetToolCatalog.from_tools([_tool("read_data")])
    unrelated = dspy.RLM("question -> answer", tools=[_tool("other")])
    with pytest.raises(RLMConfigError, match="constructed"):
        catalog.validate_constructed(unrelated)
