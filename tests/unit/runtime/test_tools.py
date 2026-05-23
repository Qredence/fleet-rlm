from __future__ import annotations

import os
import shutil
import types
from pathlib import Path
from typing import Any

import pytest


class _FakeSession:
    def __init__(self) -> None:
        self.sandbox_id = "sbx-child"
        self.write_calls: list[tuple[str, str]] = []

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        return f"/workspace/{path}"


class _FakeChild:
    def __init__(self) -> None:
        self._started = False
        self.repo_url = None
        self.session = _FakeSession()
        self._session = self.session
        self.child_isolation_metadata: dict[str, Any] = {}
        self.start_calls = 0

    def start(self) -> None:
        self._started = True
        self.start_calls += 1

    def _ensure_session_sync(self) -> _FakeSession:
        return self.session


def test_tool_marker_collection_and_name_listing() -> None:
    from fleet_rlm.runtime.tools import _collect_tools_from_modules, list_react_tool_names, tool_fn

    module = types.ModuleType("fake_tools")

    @tool_fn
    def beta_tool() -> str:
        return "beta"

    @tool_fn
    def alpha_tool() -> str:
        return "alpha"

    def helper() -> str:
        return "helper"

    module.alpha_tool = alpha_tool
    module.beta_tool = beta_tool
    module.helper = helper

    tools = _collect_tools_from_modules([module])

    assert list_react_tool_names(tools) == ["alpha_tool", "beta_tool"]


def test_discover_tools_exposes_delegate_and_chunking_tools() -> None:
    from fleet_rlm.runtime.tools import discover_tools, list_react_tool_names

    names = set(list_react_tool_names(discover_tools()))

    assert {"delegate_to_rlm", "delegate_to_rlm_batched", "chunk_document", "load_document"} <= names


def test_bind_runtime_tools_binds_memory_tools_and_skips_interpreter_only_without_interpreter() -> None:
    from fleet_rlm.runtime.tools.binding import bind_runtime_tools
    from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

    def read_core_memory() -> dict[str, Any]:
        raise AssertionError("should be rebound")

    runtime = types.SimpleNamespace(core_memory={"persona": "helpful"})
    bound = bind_runtime_tools([read_core_memory, delegate_to_rlm], runtime=runtime, interpreter=None)

    names = [getattr(tool, "name", getattr(getattr(tool, "func", tool), "__name__", "")) for tool in bound]
    assert names == ["read_core_memory"]

    result = getattr(bound[0], "func", bound[0])("persona")
    assert result == {"status": "ok", "key": "persona", "value": "helpful"}


def test_bind_runtime_tools_passes_active_interpreter_to_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.runtime.tools.binding as binding_mod
    from fleet_rlm.runtime.tools.binding import bind_runtime_tools
    from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

    captured: list[Any] = []

    def fake_delegate(*, query: str, context: str, document_url: str, interpreter: Any) -> dict[str, Any]:
        captured.append((query, interpreter))
        return {"status": "ok", "answer": query}

    monkeypatch.setattr(binding_mod, "_delegate_to_rlm", fake_delegate)

    runtime = types.SimpleNamespace(core_memory={})
    interpreter = object()
    bound = bind_runtime_tools([delegate_to_rlm], runtime=runtime, interpreter=interpreter)

    result = getattr(bound[0], "func", bound[0])(query="summarize", context="ctx", document_url="")

    assert result == {"status": "ok", "answer": "summarize"}
    assert captured == [("summarize", interpreter)]


def test_list_files_scopes_default_search_to_repo_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.tools.filesystem import list_files

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('src')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_main.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "notes" / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")

    result = list_files(pattern="**/*.py")

    assert result["status"] == "ok"
    assert result["list_files_scoped"] is True
    assert result["list_files_scope_roots"] == ["src", "tests"]
    assert result["files"] == ["src/main.py", "tests/test_main.py"]


def test_find_files_rg_cli_finds_matches(tmp_path: Path) -> None:
    if shutil.which("rg") is None:
        pytest.skip("ripgrep CLI is not available")

    from fleet_rlm.runtime.tools.filesystem import _find_files_with_rg_cli

    target = tmp_path / "sample.py"
    target.write_text("alpha = 'sandbox budget session'\n", encoding="utf-8")

    result = _find_files_with_rg_cli(pattern="sandbox.*session", path=str(tmp_path), include="*.py")

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["hits"][0]["line"] == 1
    assert "sandbox budget session" in result["hits"][0]["text"]


def test_resolve_delegate_context_embeds_small_remote_document(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.tools import rlm_delegate as delegate_mod

    monkeypatch.setattr(
        delegate_mod,
        "fetch_document_text",
        lambda url: None,
        raising=False,
    )

    import fleet_rlm.runtime.tools.document_tools as document_tools

    monkeypatch.setattr(
        document_tools,
        "fetch_document_text",
        lambda url: {"status": "ok", "text": "Document body", "char_count": 13},
    )

    child = _FakeChild()
    context = delegate_mod._resolve_delegate_context(
        child=child,
        query="summarize",
        base_context="Base context",
        document_url="https://example.com/doc.txt",
    )

    assert "Document fetched from https://example.com/doc.txt (13 chars)" in context
    assert "Document body" in context
    assert child.start_calls == 0
    assert child.session.write_calls == []


def test_resolve_delegate_context_stages_local_workspace_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.tools import rlm_delegate as delegate_mod

    child = _FakeChild()
    monkeypatch.setattr(delegate_mod, "_build_local_workspace_snapshot", lambda **_: "snapshot text")

    context = delegate_mod._resolve_delegate_context(
        child=child,
        query="inspect the repository architecture",
        base_context="Base context",
        document_url="",
    )

    assert child.start_calls == 1
    assert child.session.write_calls == [("artifacts/rlm-inputs/local_workspace_snapshot.txt", "snapshot text")]
    assert (
        child.child_isolation_metadata["local_workspace_snapshot_path"]
        == "/workspace/artifacts/rlm-inputs/local_workspace_snapshot.txt"
    )
    assert "local_workspace_snapshot.txt" in context
    assert "local host workspace" in context


def test_chunk_document_and_load_document_helpers_use_text_and_directories(tmp_path: Path) -> None:
    from fleet_rlm.runtime.tools.chunking_tools import chunk_document
    from fleet_rlm.runtime.tools.document_tools import fetch_document_text, load_document

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.txt").write_text("alpha", encoding="utf-8")
    (docs_dir / "b.md").write_text("beta", encoding="utf-8")

    chunked = chunk_document("README", strategy="size", size=10)
    listed = load_document(str(docs_dir), alias="docs")
    remote_error = fetch_document_text(str(tmp_path / "local.txt"))

    assert chunked == {
        "status": "ok",
        "strategy": "size",
        "chunk_count": 1,
        "preview": "README",
    }
    assert listed["status"] == "directory"
    assert listed["alias"] == "docs"
    assert listed["files"] == ["a.txt", "b.md"]
    assert listed["total_count"] == 2
    assert remote_error == {
        "status": "error",
        "error": "fetch_document_text only accepts HTTP(S) URLs.",
    }


def test_download_url_removes_partial_temp_file_on_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.runtime.tools import document_tools

    created: list[Path] = []

    class _Response:
        headers = {"Content-Type": "text/plain"}

        def __init__(self) -> None:
            self._chunks = [b"abcd", b"efgh"]

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, size: int) -> bytes:
            _ = size
            return self._chunks.pop(0) if self._chunks else b""

    class _Opener:
        def open(self, request: Any, timeout: int) -> _Response:
            _ = (request, timeout)
            return _Response()

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        _ = (args, kwargs)
        return [(0, 0, 0, "", ("93.184.216.34", 443))]

    def fake_mkstemp(suffix: str) -> tuple[int, str]:
        path = tmp_path / f"download{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        created.append(path)
        return fd, str(path)

    monkeypatch.setattr(document_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(document_tools.urllib.request, "build_opener", lambda *handlers: _Opener())
    monkeypatch.setattr(document_tools.tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(document_tools, "_MAX_DOWNLOAD_BYTES", 4)

    with pytest.raises(ValueError, match="exceeds"):
        document_tools._download_url("https://example.test/doc.txt")

    assert created
    assert not created[0].exists()
