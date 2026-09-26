"""Selected child inputs are copied under existing host tool authority."""

from __future__ import annotations

import time

import dspy
import pytest

from fleet_rlm.rlm.execution import materialize_child_inputs
from fleet_rlm.rlm.recursion import ChildRequest, ChildRuntimeAuthorizationError


def _tools(source: str, *, change_on_read: bool = False) -> dict[str, dspy.Tool]:
    current_source = [source]
    revision = ["revision-1"]

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path != "selected/evidence.txt":
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(current_source[0].encode("utf-8")),
                "modified_at": revision[0],
            },
        }

    def read_workspace_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        assert path == "selected/evidence.txt"
        offset = int(cursor or 0)
        if change_on_read and offset == 0:
            current_source[0] = source[:-1] + "X"
            revision[0] = "revision-2"
        current = current_source[0]
        end = min(len(current), offset + max_chars)
        return {
            "ok": True,
            "content": current[offset:end],
            "next_cursor": str(end) if end < len(current) else None,
            "eof": end >= len(current),
            "byte_size": len(current.encode("utf-8")),
        }

    return {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }


def test_child_materialization_copies_source_larger_than_two_megabytes() -> None:
    source = "record\n" * 350_000
    source_bytes = source.encode("utf-8")
    request = ChildRequest(task="Inspect all records", inputs=("selected/evidence.txt",))
    staged = materialize_child_inputs(
        request,
        tools=_tools(source),
        check_authority=lambda: None,
        turn_deadline=time.monotonic() + 10,
        source_reader=lambda _path, _max_bytes: source_bytes,
    )
    assert len(source_bytes) > 2_000_000
    assert staged == {"selected/evidence.txt": source_bytes}


def test_direct_child_reader_rejects_source_metadata_change_during_copy() -> None:
    source = b"same-size"
    revision = ["revision-1"]

    def stat_workspace_file(path: str) -> dict[str, object]:
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(source),
                "modified_at": revision[0],
            },
        }

    def read_source(_path: str, _max_bytes: int) -> bytes:
        revision[0] = "revision-2"
        return source

    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            ChildRequest(task="Inspect selected source", inputs=("selected/evidence.txt",)),
            tools={"stat_workspace_file": dspy.Tool(stat_workspace_file)},
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
            source_reader=read_source,
        )


def test_child_materialization_rejects_a_source_changed_while_copying() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("selected/evidence.txt",))
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            request,
            tools=_tools("same-size source", change_on_read=True),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_rejects_a_changed_file_in_a_directory_scope() -> None:
    source = {"selected/a.txt": "before", "selected/b.txt": "trigger"}
    revisions = {path: "revision-1" for path in source}

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path == "selected":
            return {"ok": True, "entry": {"path": path, "kind": "directory"}}
        content = source.get(path)
        if content is None:
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(content.encode("utf-8")),
                "modified_at": revisions[path],
            },
        }

    def list_workspace_files(
        path: str,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, object]:
        assert path == "selected"
        assert limit == 100
        assert after is None
        return {
            "ok": True,
            "entries": [{"path": name, "kind": "file"} for name in sorted(source)],
            "truncated": False,
            "next_cursor": None,
        }

    def read_workspace_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        current = source[path]
        if path == "selected/b.txt" and cursor is None:
            source["selected/a.txt"] = "changed"
            revisions["selected/a.txt"] = "revision-2"
        return {
            "ok": True,
            "content": current[:max_chars],
            "next_cursor": None,
            "eof": True,
            "byte_size": len(current.encode("utf-8")),
        }

    tools = {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "list_workspace_files": dspy.Tool(list_workspace_files),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            ChildRequest(task="Inspect both files", inputs=("selected",)),
            tools=tools,
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_limits_project_subtree_to_selected_scope() -> None:
    source = {
        "alpha/src/main.py": "answer = 42\n",
        "beta/private.txt": "unrelated project data",
    }

    def stat_project_file(path: str) -> dict[str, object]:
        normalized = path.removeprefix("projects/")
        if normalized == "alpha":
            return {"ok": True, "entry": {"path": normalized, "kind": "directory"}}
        content = source.get(normalized)
        if content is None:
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": normalized,
                "kind": "file",
                "byte_size": len(content.encode("utf-8")),
                "modified_at": "revision-1",
            },
        }

    def list_project_files(path: str, limit: int = 100, after: str | None = None) -> dict[str, object]:
        assert path == "projects/alpha"
        assert limit == 100
        assert after is None
        return {
            "ok": True,
            "entries": [{"path": "alpha/src/main.py", "kind": "file"}],
            "truncated": False,
            "next_cursor": None,
        }

    def read_project_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        assert cursor is None
        content = source[path.removeprefix("projects/")]
        return {
            "ok": True,
            "content": content[:max_chars],
            "next_cursor": None,
            "eof": True,
            "byte_size": len(content.encode("utf-8")),
        }

    tools = {
        "stat_project_file": dspy.Tool(stat_project_file),
        "list_project_files": dspy.Tool(list_project_files),
        "read_project_text": dspy.Tool(read_project_text),
    }
    staged = materialize_child_inputs(
        ChildRequest(task="Inspect Alpha", inputs=("projects/alpha",)),
        tools=tools,
        check_authority=lambda: None,
        turn_deadline=time.monotonic() + 10,
    )
    assert staged == {"projects/alpha/src/main.py": b"answer = 42\n"}
    assert all("beta" not in path for path in staged)


def test_child_materialization_never_stages_host_metadata_or_credential_files() -> None:
    for path in (
        ".fleet/task.json",
        "selected/.env.local",
        "selected/id_ed25519",
        "selected/.npmrc",
        "selected/.pypirc",
        "selected/.git-credentials",
        "selected/.docker/config.json",
        "selected/.git/config",
    ):
        with pytest.raises(ChildRuntimeAuthorizationError, match="credential-bearing or host metadata"):
            materialize_child_inputs(
                ChildRequest(task="Inspect source", inputs=(path,)),
                tools=_tools("irrelevant"),
                check_authority=lambda: None,
                turn_deadline=time.monotonic() + 10,
            )


def test_child_materialization_rejects_unauthorized_reference() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("unrelated/secret.txt",))
    with pytest.raises(ChildRuntimeAuthorizationError):
        materialize_child_inputs(
            request,
            tools=_tools("source"),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )
