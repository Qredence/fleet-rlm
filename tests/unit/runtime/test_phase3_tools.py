from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any


def _tool_func(tool: Any) -> Any:
    return getattr(tool, "func", tool)


def test_phase3_tools_are_registered() -> None:
    from fleet_rlm.runtime.tools import discover_tools, list_react_tool_names

    names = set(list_react_tool_names(discover_tools()))

    assert {"web_search", "fetch_page", "search_knowledge", "load_skill", "load_document"} <= names


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
    from fleet_rlm.runtime.tools.skill_tools import load_skill

    volume = tmp_path / "volume"
    (volume / "knowledge" / "ingested").mkdir(parents=True)
    (volume / "skills" / "user").mkdir(parents=True)
    (volume / "skills" / "user" / "alpha.md").write_text("alpha instructions", encoding="utf-8")
    doc = tmp_path / "doc.txt"
    doc.write_text("alpha searchable text", encoding="utf-8")
    runtime = types.SimpleNamespace(core_memory={})
    interpreter = types.SimpleNamespace(volume_mount_path=str(volume))

    bound = bind_runtime_tools([load_document, search_knowledge, load_skill], runtime=runtime, interpreter=interpreter)
    tools = {getattr(tool, "name", ""): _tool_func(tool) for tool in bound}

    loaded = tools["load_document"](str(doc), alias="alpha-doc")
    searched = tools["search_knowledge"]("searchable")
    skill = tools["load_skill"]("alpha")

    assert loaded["status"] == "ok"
    assert searched["count"] == 1
    assert skill["instructions"] == "alpha instructions"


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
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", lambda request, timeout, context=None: _Response())

    result = web_tools.fetch_page("https://example.com")

    assert result["status"] == "ok"
    assert "Title" in result["text"]
    assert "Hello world" in result["text"]
    assert "ignore" not in result["text"]
