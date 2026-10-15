from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.interpreter import DaytonaSandboxManager


def _manager() -> DaytonaSandboxManager:
    return DaytonaSandboxManager("key", "org", "volume", "session-a")


def test_allocate_child_copies_only_selected_parent_files(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "files/two.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    contents = {path: path.encode() for path in available}
    writes: list[tuple[str, bytes]] = []

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: contents[path])
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_file",
        lambda _self, path, data: writes.append((path, data)),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/two.txt", "skills/review/SKILL.md"])

    assert child.parent is parent
    assert writes == [
        ("files/two.txt", contents["files/two.txt"]),
        ("skills/review/SKILL.md", contents["skills/review/SKILL.md"]),
    ]


@pytest.mark.parametrize(
    ("input_paths", "message"),
    [
        (["artifacts/result.txt"], "beneath files/ or skills/"),
        (["files/missing.txt"], "unavailable"),
        (["files/one.txt"] * 101, "limit"),
    ],
)
def test_allocate_child_rejects_unapproved_inputs_before_provider_allocation(
    monkeypatch: pytest.MonkeyPatch, input_paths: list[str], message: str
) -> None:
    parent = _manager()
    allocations: list[DaytonaSandboxManager] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/one.txt"])
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda self: allocations.append(self))

    with pytest.raises(ValueError, match=message):
        parent.allocate_child(input_paths=input_paths)

    assert allocations == []
    assert parent._children == {}
    assert parent._allocations == 0


def test_allocate_child_default_still_copies_all_files_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    parent.allocate_child()

    assert writes == ["files/one.txt", "skills/review/SKILL.md"]


def test_factory_snapshots_inputs_and_omits_keyword_for_default_double(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.interpreter as interpreter

    root = _manager()
    active = _manager()
    received: list[Any] = []

    def allocate_selected(_self: DaytonaSandboxManager, *, input_paths: list[str]) -> DaytonaSandboxManager:
        received.append(input_paths)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_selected)
    monkeypatch.setattr(
        interpreter,
        "DaytonaInterpreter",
        lambda *, manager, tools, owns_manager: (manager, tools, owns_manager),
    )
    captured = ["files/first.txt"]
    factory = root.interpreter_factory(input_paths=captured)
    captured.append("files/later.txt")
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        result = factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert received == [["files/first.txt"]]
    assert result[0] is root
    assert result[2] is True


def test_factory_default_calls_allocate_child_without_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.interpreter as interpreter

    root = _manager()
    active = _manager()
    calls: list[bool] = []

    def allocate_default(_self: DaytonaSandboxManager) -> DaytonaSandboxManager:
        calls.append(True)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_default)
    monkeypatch.setattr(
        interpreter,
        "DaytonaInterpreter",
        lambda *, manager, tools, owns_manager: (manager, tools, owns_manager),
    )
    factory = root.interpreter_factory()
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert calls == [True]


def test_factory_without_active_root_keeps_existing_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.interpreter as interpreter

    root = _manager()
    monkeypatch.setattr(
        interpreter,
        "DaytonaInterpreter",
        lambda *, manager, tools, owns_manager: (manager, tools, owns_manager),
    )

    result = root.interpreter_factory(input_paths=["files/input.txt"])()

    assert result[0] is root
    assert result[2] is False
