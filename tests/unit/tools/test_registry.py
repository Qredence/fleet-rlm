from __future__ import annotations

from fleet_rlm.tools.registry import (
    ToolExposurePolicy,
    ToolRuntimeContext,
    descriptor_by_name,
    list_exposed_tool_descriptors,
)


def test_tool_registry_lists_existing_skill_tools() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=True)}

    assert {"list_skills", "load_skill", "read_skill_resource", "run_skill_script"} <= names


def test_tool_registry_filters_sandbox_tools_without_sandbox() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=False)}

    assert "list_skills" in names
    assert "list_files" not in names
    assert "read_file" not in names
    assert "run_skill_script" not in names


def test_write_enabled_tools_are_hidden_by_default() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=True)}

    assert "write_file" not in names
    assert "sandbox_write_file" not in names
    assert "create_artifact" not in names
    assert "update_artifact" not in names
    assert "list_artifacts" in names
    assert "read_artifact" in names


def test_policy_can_explicitly_enable_write_tool() -> None:
    context = ToolRuntimeContext(
        sandbox_available=True,
        policy=ToolExposurePolicy(enabled_tool_names=["write_file"]),
    )

    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(context=context)}

    assert "write_file" in names


def test_policy_allow_write_tools_enables_artifact_writes() -> None:
    context = ToolRuntimeContext(
        sandbox_available=True,
        policy=ToolExposurePolicy(allow_write_tools=True),
    )

    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(context=context)}

    assert "create_artifact" in names
    assert "update_artifact" in names
    assert "sandbox_delete_file" not in names
    assert "sandbox_write_file" not in names
    assert "write_file" not in names


def test_policy_can_explicitly_enable_destructive_sandbox_write_tool() -> None:
    context = ToolRuntimeContext(
        sandbox_available=True,
        policy=ToolExposurePolicy(enabled_tool_names=["sandbox_delete_file"]),
    )

    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(context=context)}

    assert "sandbox_delete_file" in names


def test_allowed_capabilities_filter_tools() -> None:
    context = ToolRuntimeContext(
        sandbox_available=True,
        policy=ToolExposurePolicy(allowed_capabilities=["skills:read"]),
    )

    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(context=context)}

    assert {"list_skills", "load_skill", "read_skill_resource"} <= names
    assert "list_files" not in names
    assert "run_skill_script" not in names
    assert "web_search" not in names
    assert "execute_code" not in names


def test_discovered_tools_have_policy_descriptors() -> None:
    from fleet_rlm.runtime.tools.registry import _discover_unfiltered_tools, list_react_tool_names

    discovered = set(list_react_tool_names(list(_discover_unfiltered_tools())))
    catalogued = set(descriptor_by_name())

    missing = discovered - catalogued
    assert not missing, f"Tools missing descriptors: {sorted(missing)}"
