"""Unit contracts for the bounded public-text URL Tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from fleet_rlm.files.url_tool import (
    InMemoryUrlSourceStore,
    UrlFetchResult,
    UrllibPublicTextFetcher,
    UrlToolError,
    UrlToolHost,
    WorkspaceUrlSourceStore,
)
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.rlm.events import ToolCompleted, ToolStarted
from fleet_rlm.rlm.tool_observer import observe_tool


@dataclass
class _FakeFetcher:
    calls: list[str]
    text: str = "needle: 42"

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        assert max_bytes > 0
        self.calls.append(url)
        return UrlFetchResult(url, "text/plain; charset=utf-8", self.text)


class _FakeWorkspace:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def stat(self, path: str) -> WorkspaceEntry | None:
        value = self.values.get(path)
        return None if value is None else WorkspaceEntry(path, "file", len(value.encode()), None)

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        del after
        prefix = path.rstrip("/") + "/"
        entries = tuple(
            WorkspaceEntry(item, "file", len(value.encode()), None)
            for item, value in self.values.items()
            if item.startswith(prefix)
        )
        return WorkspaceListResult(entries[:limit], truncated=len(entries) > limit, next_cursor=None)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        value = self.values[path]
        data = value.encode()
        if len(data) > max_bytes:
            raise ValueError("too large")
        offset = int(cursor or "0")
        content = value[offset : offset + max_chars]
        next_offset = offset + len(content)
        return WorkspaceTextPage(
            content,
            None if next_offset >= len(value) else str(next_offset),
            len(data),
            next_offset >= len(value),
        )

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        if path in self.values and not overwrite:
            raise FileExistsError(path)
        self.values[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), None)


class _MissingParentWorkspace(_FakeWorkspace):
    def stat(self, path: str) -> WorkspaceEntry | None:
        del path
        raise FileNotFoundError("sources/urls")


class _MissingCacheDirectoryWorkspace(_FakeWorkspace):
    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        del path, limit, after
        raise FileNotFoundError("sources/urls")


def test_in_memory_url_store_bounds_entries_across_sessions_with_lru_recency() -> None:
    store = InMemoryUrlSourceStore(max_entries_per_session=8, max_entries_total=2, max_bytes_total=100)
    first_session = uuid4()
    second_session = uuid4()

    store.write(first_session, "sources/urls/first.txt", "first", max_bytes=100)
    store.write(second_session, "sources/urls/second.txt", "second", max_bytes=100)
    assert store.read(first_session, "sources/urls/first.txt", max_bytes=100) == "first"

    store.write(second_session, "sources/urls/third.txt", "third", max_bytes=100)

    assert store.read(first_session, "sources/urls/first.txt", max_bytes=100) == "first"
    assert store.read(second_session, "sources/urls/second.txt", max_bytes=100) is None
    assert store.read(second_session, "sources/urls/third.txt", max_bytes=100) == "third"


def test_in_memory_url_store_bounds_total_cached_bytes() -> None:
    store = InMemoryUrlSourceStore(max_entries_per_session=8, max_entries_total=8, max_bytes_total=6)
    session_id = uuid4()

    store.write(session_id, "sources/urls/first.txt", "first", max_bytes=100)
    store.write(session_id, "sources/urls/second.txt", "second", max_bytes=100)

    assert store.read(session_id, "sources/urls/first.txt", max_bytes=100) is None
    assert store.read(session_id, "sources/urls/second.txt", max_bytes=100) == "second"


def test_workspace_url_store_stops_growing_when_entry_bound_is_reached() -> None:
    session_id = uuid4()
    workspace = _FakeWorkspace()
    fetcher = _FakeFetcher([])
    store = WorkspaceUrlSourceStore(workspace, max_entries_total=1, max_bytes_total=100)
    first = UrlToolHost(session_id=session_id, store=store, max_bytes=1_024, fetcher=fetcher).as_tools()[0]
    second = UrlToolHost(session_id=session_id, store=store, max_bytes=1_024, fetcher=fetcher).as_tools()[0]

    assert first(url="https://example.com/first")["cache_hit"] is False
    assert second(url="https://example.com/second")["cache_hit"] is False
    assert second(url="https://example.com/second")["cache_hit"] is False
    assert fetcher.calls == ["https://example.com/first", "https://example.com/second", "https://example.com/second"]


def test_url_tool_returns_content_to_repl_but_projects_metadata_only() -> None:
    session_id = uuid4()
    fetcher = _FakeFetcher([])
    host = UrlToolHost(
        session_id=session_id,
        store=WorkspaceUrlSourceStore(_FakeWorkspace()),
        max_bytes=1_024,
        fetcher=fetcher,
    )
    observed: list[Any] = []
    tool = host.as_tools()[0]
    wrapped = observe_tool(tool, observed.append, host.event_views()["fetch_url"])

    result = wrapped(url="https://example.com/report")

    assert result["ok"] is True
    assert result["content"] == "needle: 42"
    assert result["workspace_path"].startswith("sources/urls/")
    assert fetcher.calls == ["https://example.com/report"]
    assert [type(item) for item in observed] == [ToolStarted, ToolCompleted]
    assert observed[0].input == {"source_id": result["source_id"]}
    assert observed[1].output == {
        "ok": True,
        "source_id": result["source_id"],
        "cache_hit": False,
        "content_type": "text/plain; charset=utf-8",
        "byte_size": len("needle: 42"),
        "checksum_sha256": result["checksum_sha256"],
    }
    assert "needle: 42" not in str(observed)
    assert "https://example.com/report" not in str(observed)


def test_workspace_url_store_reuses_content_across_tool_hosts() -> None:
    session_id = uuid4()
    workspace = _FakeWorkspace()
    fetcher = _FakeFetcher([])
    store = WorkspaceUrlSourceStore(workspace)
    first = UrlToolHost(session_id=session_id, store=store, max_bytes=1_024, fetcher=fetcher).as_tools()[0]
    second = UrlToolHost(session_id=session_id, store=store, max_bytes=1_024, fetcher=fetcher).as_tools()[0]

    assert first(url="https://example.com/report")["cache_hit"] is False
    cached = second(url="https://example.com/report")

    assert cached["cache_hit"] is True
    assert cached["content"] == "needle: 42"
    assert "content_type" not in cached
    assert fetcher.calls == ["https://example.com/report"]


def test_workspace_url_store_refetches_when_workspace_content_was_overwritten() -> None:
    session_id = uuid4()
    workspace = _FakeWorkspace()
    fetcher = _FakeFetcher([])
    store = WorkspaceUrlSourceStore(workspace)
    tool = UrlToolHost(session_id=session_id, store=store, max_bytes=1_024, fetcher=fetcher).as_tools()[0]

    first = tool(url="https://example.com/report")
    workspace.values[first["workspace_path"]] = "model-authored replacement"
    second = tool(url="https://example.com/report")

    assert first["cache_hit"] is False
    assert second["cache_hit"] is False
    assert second["content"] == "needle: 42"
    assert fetcher.calls == ["https://example.com/report", "https://example.com/report"]


def test_workspace_url_store_treats_a_missing_cache_parent_as_a_cache_miss() -> None:
    fetcher = _FakeFetcher([])
    tool = UrlToolHost(
        session_id=uuid4(),
        store=WorkspaceUrlSourceStore(_MissingParentWorkspace()),
        max_bytes=1_024,
        fetcher=fetcher,
    ).as_tools()[0]

    result = tool(url="https://example.com/report")

    assert result["ok"] is True
    assert result["cache_hit"] is False
    assert fetcher.calls == ["https://example.com/report"]


def test_workspace_url_store_creates_a_cache_when_directory_listing_is_missing() -> None:
    fetcher = _FakeFetcher([])
    tool = UrlToolHost(
        session_id=uuid4(),
        store=WorkspaceUrlSourceStore(_MissingCacheDirectoryWorkspace()),
        max_bytes=1_024,
        fetcher=fetcher,
    ).as_tools()[0]

    result = tool(url="https://example.com/report")

    assert result["ok"] is True
    assert result["cache_hit"] is False
    assert fetcher.calls == ["https://example.com/report"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/report",
        "https://user:password@example.com/report",
        "https://example.com:8443/report",
        "not a url",
    ],
)
def test_url_tool_rejects_unsafe_url_shapes(url: str) -> None:
    fetcher = _FakeFetcher([])
    tool = UrlToolHost(
        session_id=uuid4(),
        store=WorkspaceUrlSourceStore(_FakeWorkspace()),
        max_bytes=1_024,
        fetcher=fetcher,
    ).as_tools()[0]

    result = tool(url=url)

    assert result["ok"] is False
    assert result["error"] == "invalid_url"
    assert fetcher.calls == []


def test_public_fetcher_rejects_private_dns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.files.url_tool.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("127.0.0.1", 443))],
    )

    with pytest.raises(Exception, match="public addresses"):
        UrllibPublicTextFetcher().fetch("https://example.com/report", max_bytes=1_024)


def test_public_fetcher_streams_and_pins_validated_address(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "fleet_rlm.files.url_tool.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("93.184.216.34", 443))],
    )

    class Response:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"Content-Type": "text/plain; charset=utf-8"}

        def stream(self, _size: int, *, decode_content: bool):
            assert decode_content is True
            return iter((b"hello ", b"world"))

        def release_conn(self) -> None:
            calls["released"] = True

        def close(self) -> None:
            calls["closed"] = True

    class Pool:
        def __init__(self, host: str, **kwargs: object) -> None:
            calls["host"] = host
            calls["pool_kwargs"] = kwargs

        def close(self) -> None:
            calls["pool_closed"] = True

        def urlopen(self, method: str, target: str, **kwargs: object) -> Response:
            calls["method"] = method
            calls["target"] = target
            calls["request_kwargs"] = kwargs
            return Response()

    monkeypatch.setattr("fleet_rlm.files.url_tool.urllib3.HTTPSConnectionPool", Pool)

    result = UrllibPublicTextFetcher().fetch("https://example.com/report", max_bytes=1_024)

    assert result.text == "hello world"
    assert calls["host"] == "93.184.216.34"
    assert calls["method"] == "GET"
    assert calls["target"] == "/report"
    assert calls["released"] is True
    assert calls["closed"] is True
    assert calls["pool_closed"] is True


def test_public_fetcher_enforces_total_wall_clock_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "fleet_rlm.files.url_tool.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("93.184.216.34", 443))],
    )
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr("fleet_rlm.files.url_tool.time.monotonic", lambda: next(clock))

    class Response:
        status = 200
        headers: ClassVar[dict[str, str]] = {"Content-Type": "text/plain"}

        def stream(self, _size: int, *, decode_content: bool):
            del decode_content
            return iter((b"late",))

        def release_conn(self) -> None:
            pass

        def close(self) -> None:
            pass

    class Pool:
        def __init__(self, _host: str, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

        def urlopen(self, _method: str, _target: str, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr("fleet_rlm.files.url_tool.urllib3.HTTPSConnectionPool", Pool)

    with pytest.raises(UrlToolError, match="time limit"):
        UrllibPublicTextFetcher(timeout_seconds=1).fetch("https://example.com/report", max_bytes=1_024)
