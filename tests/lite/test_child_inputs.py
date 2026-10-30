from __future__ import annotations

from typing import Any

import pytest

from fleet_rlm.sandbox.manager import DaytonaSandboxManager


@pytest.fixture(autouse=True)
def _batches_use_single_file_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests fake per-file transfers; route the batched child copy through those fakes."""
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda self, paths, **_limits: {path: self.read_workspace_file(path) for path in paths},
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [self.write_workspace_file(path, data) for path, data in files.items()] and None,
    )


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
        (["memory/notes.txt"], "beneath files/, skills/ or artifacts/"),
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


def test_selected_artifacts_are_copied_and_unchanged_copies_are_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    parent_files = {"files/sales.csv": b"csv", "artifacts/report.json": b'{"north": 1}'}
    child_files: dict[str, bytes] = {}
    promoted: list[str] = []

    def list_files(self: DaytonaSandboxManager) -> list[str]:
        return list(child_files if self.parent is not None else parent_files)

    def read(self: DaytonaSandboxManager, path: str, **_limits: Any) -> bytes:
        return (child_files if self.parent is not None else parent_files)[path]

    def write(self: DaytonaSandboxManager, path: str, data: bytes) -> None:
        if self.parent is not None:
            child_files[path] = data
        else:
            promoted.append(path)

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", list_files)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", read)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [write(self, p, b) for p, b in files.items()],
    )
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "[]")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv", "artifacts/report.json"])
    assert child_files == parent_files
    child_files["artifacts/counterfactual.json"] = b'{"central": 2}'

    results = child.collect_child_artifacts()

    assert [row["path"] for row in results] == [f"artifacts/{child.agent_id}/counterfactual.json"]
    assert promoted == [f"artifacts/{child.agent_id}/counterfactual.json"]


def test_explicit_children_always_get_skills_without_receipting_them_as_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    available = ["files/sales.csv", "files/other.csv", "skills/tabular-analysis/SKILL.md"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv"])

    assert writes == ["files/sales.csv", "skills/tabular-analysis/SKILL.md"]
    assert list(parent._child_receipts[child.agent_id]["inputs"]) == ["files/sales.csv"]


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
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    received: list[Any] = []

    def allocate_selected(_self: DaytonaSandboxManager, *, input_paths: list[str]) -> DaytonaSandboxManager:
        received.append(input_paths)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_selected)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths, rlm_defaults: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
            rlm_defaults,
        ),
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
    assert result[3] == ("files/first.txt",)


def test_factory_default_calls_allocate_child_without_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    calls: list[bool] = []

    def allocate_default(_self: DaytonaSandboxManager) -> DaytonaSandboxManager:
        calls.append(True)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_default)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths, rlm_defaults: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
            rlm_defaults,
        ),
    )
    factory = root.interpreter_factory()
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert calls == [True]


def test_factory_without_active_root_keeps_existing_manager(monkeypatch: pytest.MonkeyPatch) -> None:

    root = _manager()
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths, rlm_defaults: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
            rlm_defaults,
        ),
    )

    result = root.interpreter_factory(input_paths=["files/input.txt"])()

    assert result[0] is root
    assert result[2] is False
