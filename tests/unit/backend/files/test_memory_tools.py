"""Behavioral seams for workspace-memory host Tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import dspy
import pytest

from fleet_rlm.files.memory_models import (
    WorkspaceMemoryAppendResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryStoreFullError,
    WorkspaceMemoryStoreUnavailableError,
)
from fleet_rlm.rlm.tool_observer import observe_tool


@dataclass
class FakeMemoryStore:
    read_result: WorkspaceMemoryReadResult = field(
        default_factory=lambda: WorkspaceMemoryReadResult("", False, 0, 262_144, 0)
    )
    append_result: WorkspaceMemoryAppendResult = field(default_factory=lambda: WorkspaceMemoryAppendResult(0, 0))
    appended: list[str] | None = None
    failure: BaseException | None = None

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
        assert byte_budget == 262_144
        if self.failure is not None:
            raise self.failure
        return self.read_result

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult:
        if self.failure is not None:
            raise self.failure
        if self.appended is None:
            self.appended = []
        self.appended.append(record)
        return self.append_result


def _host(store: FakeMemoryStore | None = None):
    from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost

    return WorkspaceMemoryToolHost(
        store or FakeMemoryStore(),
        clock=lambda: datetime(2026, 7, 27, 11, 14, 5, tzinfo=UTC),
    )


def test_exposes_exact_memory_tool_contracts() -> None:
    tools = _host().as_tools()

    assert tuple(str(tool.name) for tool in tools) == ("read_workspace_memory", "update_workspace_memory")
    assert all(type(tool) is dspy.Tool for tool in tools)
    assert tools[0].args == {}
    assert tools[1].args == {
        "key_learning": {"type": "string"},
        "category": {"type": "string"},
    }
    assert "only when the user explicitly requests" in tools[1].desc


def test_reads_missing_memory_as_successful_empty_result() -> None:
    tool = _host().as_tools()[0]

    assert tool() == {
        "ok": True,
        "namespace": "workspace_memory",
        "content": "",
        "truncated": False,
        "bytes_returned": 0,
        "byte_budget": 262_144,
        "total_bytes": 0,
    }


def test_normalizes_and_records_a_timestamped_learning() -> None:
    store = FakeMemoryStore(append_result=WorkspaceMemoryAppendResult(70, 70))
    tool = _host(store).as_tools()[1]

    result = tool(key_learning="  Prefers\n\tpolars   for  dataframes. ", category="User Preference")

    assert store.appended == ["- [2026-07-27T11:14:05Z] **User Preference**: Prefers polars for dataframes.\n"]
    assert result == {
        "ok": True,
        "namespace": "workspace_memory",
        "category": "User Preference",
        "entry_bytes": 70,
        "total_bytes": 70,
    }


@pytest.mark.parametrize(
    ("key_learning", "category", "code", "message"),
    [
        (" \t\n", "General", "invalid_entry", "Workspace Memory entry is invalid"),
        ("contains\x00nul", "General", "invalid_entry", "Workspace Memory entry is invalid"),
        ("learning", "", "invalid_category", "Workspace Memory category is invalid"),
        ("learning", "x" * 65, "invalid_category", "Workspace Memory category is invalid"),
        ("learning", "**not a label**", "invalid_category", "Workspace Memory category is invalid"),
        pytest.param(
            "lone surrogate \ud800",
            "General",
            "invalid_entry",
            "Workspace Memory entry is invalid",
            id="lone-surrogate-learning",
        ),
        pytest.param(
            "learning",
            "lone surrogate \ud800",
            "invalid_category",
            "Workspace Memory category is invalid",
            id="lone-surrogate-category",
        ),
    ],
)
def test_rejects_invalid_entry_and_category_values(
    key_learning: str,
    category: str,
    code: str,
    message: str,
) -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError, match=message) as error:
        _host().as_tools()[1](key_learning=key_learning, category=category)

    assert error.value.code == code
    assert error.value.public_message == message


def test_rejects_a_formatted_record_larger_than_4kib() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError) as error:
        _host().as_tools()[1](key_learning="x" * 4_096)

    assert error.value.code == "invalid_entry"


@pytest.mark.parametrize(
    ("failure", "code", "message"),
    [
        (WorkspaceMemoryStoreFullError(), "full", "Workspace Memory is full"),
        (WorkspaceMemoryStoreUnavailableError(), "unavailable", "Workspace Memory is unavailable"),
    ],
)
def test_maps_closed_storage_failures(failure: BaseException, code: str, message: str) -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    with pytest.raises(MemoryToolError, match=message) as error:
        _host(FakeMemoryStore(failure=failure)).as_tools()[1](key_learning="remember this")

    assert error.value.code == code
    assert error.value.public_message == message


def test_event_views_expose_only_memory_metadata() -> None:
    from fleet_rlm.files.memory_tools import MemoryToolError

    secret = "private learning at /home/daytona/fleet/MEMORIES.md"
    store = FakeMemoryStore(
        read_result=WorkspaceMemoryReadResult(secret, True, len(secret.encode()), 262_144, 300_000),
        append_result=WorkspaceMemoryAppendResult(75, 300_075),
    )
    host = _host(store)
    tools = {str(tool.name): tool for tool in host.as_tools()}
    views = host.event_views()
    observed: list[object] = []

    read = observe_tool(tools["read_workspace_memory"], observed.append, views["read_workspace_memory"])
    update = observe_tool(tools["update_workspace_memory"], observed.append, views["update_workspace_memory"])
    read()
    update(key_learning=secret, category="Preference")

    assert observed[0].input == {}
    assert observed[1].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "truncated": True,
        "bytes_returned": len(secret.encode()),
        "byte_budget": 262_144,
        "total_bytes": 300_000,
    }
    assert observed[2].input == {"category": "Preference", "key_learning_bytes": len(secret.encode())}
    assert observed[3].output == {
        "ok": True,
        "namespace": "workspace_memory",
        "category": "Preference",
        "entry_bytes": 75,
        "total_bytes": 300_075,
    }
    assert secret not in str(observed)
    assert "/home/daytona" not in str(observed)

    observed.clear()
    failed_host = _host(FakeMemoryStore(failure=WorkspaceMemoryStoreUnavailableError("provider details")))
    failed_update = observe_tool(
        {str(tool.name): tool for tool in failed_host.as_tools()}["update_workspace_memory"],
        observed.append,
        failed_host.event_views()["update_workspace_memory"],
    )
    with pytest.raises(MemoryToolError):
        failed_update(key_learning="private learning", category="Preference")
    assert observed[1].error == "Workspace Memory is unavailable"
    assert "provider details" not in str(observed)

    observed.clear()
    with pytest.raises(MemoryToolError):
        update(key_learning="private learning", category="/home/daytona/private")
    assert observed[0].input == {"category": "invalid", "key_learning_bytes": 16}
    assert "/home/daytona" not in str(observed)
