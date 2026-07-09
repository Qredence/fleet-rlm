from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest


def _tool_func(tool: Any) -> Any:
    return getattr(tool, "func", tool)


def test_phase3_tools_are_registered() -> None:
    from fleet_rlm.runtime.tools import discover_tools, list_react_tool_names

    names = set(list_react_tool_names(discover_tools(sandbox_available=True)))

    assert {
        "web_search",
        "fetch_page",
        "search_knowledge",
        "load_skill",
        "load_document",
        "list_skills",
        "read_skill_resource",
        "run_skill_script",
    } <= names


def test_load_document_persists_and_searches_knowledge(tmp_path: Path) -> None:
    from fleet_rlm.runtime.tools.document_tools import _load_document_impl
    from fleet_rlm.runtime.tools.knowledge_tools import _search_knowledge_impl

    volume = tmp_path / "volume"
    (volume / "knowledge" / "ingested").mkdir(parents=True)
    doc = tmp_path / "source.txt"
    doc.write_text("Fleet RLM durable knowledge alpha", encoding="utf-8")

    loaded = _load_document_impl(str(doc), alias="source", volume_mount_path=str(volume))
    searched = _search_knowledge_impl("durable knowledge", volume_mount_path=str(volume))

    assert loaded.status == "ok"
    assert loaded.knowledge.doc_id.startswith("doc_")
    assert (volume / "knowledge" / "index.json").exists()
    assert searched.status == "ok"
    assert searched.count == 1
    assert searched.results[0].alias == "source"


def test_load_skill_prefers_user_skill(tmp_path: Path) -> None:
    from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl

    volume = tmp_path / "volume"
    (volume / "skills" / "system").mkdir(parents=True)
    (volume / "skills" / "user").mkdir(parents=True)
    (volume / "skills" / "system" / "review.md").write_text("system skill", encoding="utf-8")
    (volume / "skills" / "user" / "review.md").write_text("user skill", encoding="utf-8")

    result = _load_skill_impl("review", volume_mount_path=str(volume))

    assert result.status == "ok"
    assert result.name == "review"
    assert result.scope == "user"
    assert result.path == str(volume / "skills" / "user" / "review.md")
    assert result.instructions == "user skill"


def test_bind_runtime_tools_binds_phase3_volume_tools(tmp_path: Path) -> None:
    from fleet_rlm.runtime.tools.binding import bind_runtime_tools
    from fleet_rlm.runtime.tools.document_tools import load_document
    from fleet_rlm.runtime.tools.knowledge_tools import search_knowledge
    from fleet_rlm.runtime.tools.skill_tools import list_skills, load_skill, read_skill_resource

    volume = tmp_path / "volume"
    (volume / "knowledge" / "ingested").mkdir(parents=True)
    skill_dir = volume / "skills" / "user" / "alpha"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        '---\nname: alpha\ndescription: "Alpha skill"\n---\n\n# Alpha\n',
        encoding="utf-8",
    )
    refs = skill_dir / "references"
    refs.mkdir()
    refs.joinpath("note.md").write_text("alpha reference body", encoding="utf-8")
    doc = tmp_path / "doc.txt"
    doc.write_text("alpha searchable text", encoding="utf-8")
    runtime = types.SimpleNamespace(core_memory={})
    interpreter = types.SimpleNamespace(volume_mount_path=str(volume))

    bound = bind_runtime_tools(
        [load_document, search_knowledge, load_skill, list_skills, read_skill_resource],
        runtime=runtime,
        interpreter=interpreter,
    )
    tools = {getattr(tool, "name", ""): _tool_func(tool) for tool in bound}

    loaded = tools["load_document"](str(doc), alias="alpha-doc")
    searched = tools["search_knowledge"]("searchable")
    skill = tools["load_skill"]("alpha")
    listed = tools["list_skills"]()
    resource = tools["read_skill_resource"]("alpha", "references/note.md")

    assert loaded["status"] == "ok"
    assert searched["count"] == 1
    assert skill["instructions"].startswith("---")
    assert skill["resources"]
    assert any(item["name"] == "alpha" for item in listed["skills"])
    assert resource["status"] == "ok"
    assert resource["content"] == "alpha reference body"


def test_bind_run_skill_script_uses_per_turn_selected_skill_ids(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from fleet_rlm.runtime.tools.binding import bind_runtime_tools
    from fleet_rlm.runtime.tools.skill_tools import run_skill_script

    volume = tmp_path / "volume"
    skill_dir = volume / "skills" / "system" / "alpha"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        '---\nname: alpha\ndescription: "Alpha skill"\n---\n\n# Alpha\n',
        encoding="utf-8",
    )
    scripts.joinpath("run.py").write_text("print('ok')\n", encoding="utf-8")

    interpreter = SimpleNamespace(
        volume_mount_path=str(volume),
        delegate_result_truncation_chars=8000,
        execute=lambda code, variables=None: SimpleNamespace(
            output={
                "success": True,
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }
        ),
    )
    runtime = SimpleNamespace(core_memory={}, _selected_skill_ids=[])

    bound = bind_runtime_tools([run_skill_script], runtime=runtime, interpreter=interpreter)
    tools = {getattr(tool, "name", ""): _tool_func(tool) for tool in bound}
    runtime._selected_skill_ids = ["alpha"]

    payload = tools["run_skill_script"]("alpha", "scripts/run.py")

    assert payload["success"] is True
    assert payload["exit_code"] == 0


def test_phase3_volume_tools_use_env_volume_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.tools.knowledge_tools import persist_knowledge_document, search_knowledge
    from fleet_rlm.runtime.tools.skill_tools import load_skill

    volume = tmp_path / "volume"
    (volume / "knowledge" / "ingested").mkdir(parents=True)
    (volume / "skills" / "user").mkdir(parents=True)
    (volume / "skills" / "user" / "phase3.md").write_text("phase3 skill instructions", encoding="utf-8")
    persist_knowledge_document(
        source="unit://phase3",
        text="phase3 env fallback searchable text",
        metadata={"kind": "unit"},
        volume_mount_path=str(volume),
    )
    monkeypatch.setenv("FLEET_RLM_VOLUME_MOUNT_PATH", str(volume))
    monkeypatch.delenv("DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH", raising=False)

    searched = search_knowledge("env fallback")
    skill = load_skill("phase3")

    assert searched["status"] == "ok"
    assert searched["count"] == 1
    assert searched["results"][0]["source"] == "unit://phase3"
    assert skill["status"] == "ok"
    assert skill["scope"] == "user"
    assert skill["instructions"] == "phase3 skill instructions"


def test_memory_tools_require_runtime_binding() -> None:
    from fleet_rlm.runtime.tools.volume_memory_tools import recall, remember

    with pytest.raises(RuntimeError, match="bound volume_mount_path"):
        remember("phase3", "root-only")
    with pytest.raises(RuntimeError, match="bound volume_mount_path"):
        recall("phase3")


def test_web_search_reports_missing_brave_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.tools import web_tools

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    result = web_tools.web_search("fleet rlm")

    assert result["status"] == "error"
    assert result["provider"] == "brave"
    assert result["count"] == 0
    assert "BRAVE_SEARCH_API_KEY or BRAVE_API_KEY" in result["error"]


def test_web_search_uses_brave_api(monkeypatch: Any) -> None:
    from fleet_rlm.runtime.tools import web_tools

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"web": {"results": [{"url": "https://example.com", "title": "Example", "description": "Snippet"}]}}
            ).encode("utf-8")

    captured: list[Any] = []

    def fake_urlopen(request: Any, timeout: int, context: Any = None) -> _Response:
        captured.append((request, timeout))
        return _Response()

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret")
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", fake_urlopen)

    result = web_tools.web_search("fleet rlm", max_results=1)

    assert result["status"] == "ok"
    assert result["provider"] == "brave"
    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://example.com"
    assert captured[0][1] == 20


def test_fetch_page_extracts_text(monkeypatch: Any) -> None:
    from fleet_rlm.runtime.tools import web_tools

    class _Headers(dict[str, str]):
        def get(self, key: str, default: str = "") -> str:
            return super().get(key, default)

    class _Response:
        headers = _Headers({"Content-Type": "text/html; charset=utf-8"})

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        def read(self, size: int) -> bytes:
            _ = size
            return b"<html><script>ignore()</script><body><h1>Title</h1><p>Hello world</p></body></html>"

    monkeypatch.setattr(web_tools, "_validate_download_url", lambda url: None)
    monkeypatch.setattr(web_tools, "_open_fetch_request", lambda request, timeout, context: _Response())

    result = web_tools.fetch_page("https://example.com")

    assert result["status"] == "ok"
    assert "Title" in result["text"]
    assert "Hello world" in result["text"]
    assert "ignore" not in result["text"]


def test_fetch_page_uses_validating_redirect_handler(monkeypatch: Any) -> None:
    from fleet_rlm.runtime.tools import web_tools

    class _Headers(dict[str, str]):
        def get(self, key: str, default: str = "") -> str:
            return super().get(key, default)

    class _Response:
        headers = _Headers({"Content-Type": "text/html; charset=utf-8"})

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        def read(self, size: int) -> bytes:
            _ = size
            return b"<html><body><p>Safe page</p></body></html>"

    class _Opener:
        def open(self, request: Any, timeout: int) -> _Response:
            _ = request, timeout
            return _Response()

    handlers: list[Any] = []

    def fake_build_opener(*args: Any) -> _Opener:
        handlers.extend(args)
        return _Opener()

    monkeypatch.setattr(web_tools, "_validate_download_url", lambda url: None)
    monkeypatch.setattr(web_tools.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(web_tools.urllib.request, "HTTPSHandler", lambda context: ("https-handler", context))

    result = web_tools.fetch_page("https://example.com")

    assert result["status"] == "ok"
    assert any(isinstance(handler, web_tools._ValidatingRedirectHandler) for handler in handlers)
