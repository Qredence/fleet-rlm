from __future__ import annotations

import pytest

from fleet_rlm.tools.registry import (
    ToolExposurePolicy,
    ToolRuntimeContext,
    list_exposed_tool_descriptors,
)


def test_artifact_write_tools_hidden_by_default() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=True)}

    assert "create_artifact" not in names
    assert "update_artifact" not in names


def test_artifact_write_tools_appear_when_write_policy_enabled() -> None:
    context = ToolRuntimeContext(
        sandbox_available=True,
        policy=ToolExposurePolicy(allow_write_tools=True),
    )

    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(context=context)}

    assert "create_artifact" in names
    assert "update_artifact" in names
    assert "sandbox_delete_file" not in names


def test_artifact_read_tools_exposed_with_sandbox_by_default() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=True)}

    assert "list_artifacts" in names
    assert "read_artifact" in names


def test_artifact_read_tools_hidden_without_sandbox() -> None:
    names = {descriptor.name for descriptor in list_exposed_tool_descriptors(sandbox_available=False)}

    assert "list_artifacts" not in names
    assert "read_artifact" not in names


def test_all_discovered_tools_have_descriptors_including_artifacts() -> None:
    from fleet_rlm.runtime.tools.registry import _discover_unfiltered_tools, list_react_tool_names
    from fleet_rlm.tools.registry import descriptor_by_name

    discovered = set(list_react_tool_names(list(_discover_unfiltered_tools())))
    catalogued = set(descriptor_by_name())

    missing = discovered - catalogued
    assert not missing, f"Tools missing descriptors: {sorted(missing)}"
    assert {"create_artifact", "update_artifact", "list_artifacts", "read_artifact"} <= catalogued


def test_bind_runtime_tools_passes_session_id_to_create_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from dspy import Tool

    from fleet_rlm.runtime.tools.binding import bind_runtime_tools

    captured: dict[str, object] = {}

    def fake_create_artifact_impl(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.binding.create_artifact_impl",
        fake_create_artifact_impl,
    )

    runtime = SimpleNamespace(_db_session_id="sess-chat-1", core_memory={})
    interpreter = SimpleNamespace(_session=SimpleNamespace(), volume_mount_path="/home/daytona/memory")
    tool = Tool(lambda: None, name="create_artifact")
    bound = bind_runtime_tools([tool], runtime=runtime, interpreter=interpreter)

    create_tool = next(item for item in bound if getattr(item, "name", None) == "create_artifact")
    payload = create_tool.func(category="reports", relative_path="summary.md", content="# hi")

    assert payload["status"] == "ok"
    assert captured["session_id"] == "sess-chat-1"
    assert captured["interpreter"] is interpreter
