"""Shared fakes, fixture-builders, and SSE helpers for chat API tests.

Extracted from the formerly duplicated definitions in ``test_chat_sse.py`` and
``test_cross_flows.py`` during Phase 2A.2 test/contract cleanup. WebSocket
tests (``test_ws_stream_events.py`` and friends) define their own independent
fakes and do not use this module.

The autouse chat-router stubs (``install_chat_agent_context_stub`` and
``install_prepare_chat_runtime_stub``) are plain functions rather than
``@pytest.fixture``s: each consumer file wraps them in its own thin
``@pytest.fixture(autouse=True)`` so the monkeypatching stays scoped to that
file instead of leaking to every other test module under
``tests/unit/api/``.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.dependencies import require_http_identity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

DEFAULT_BODY: dict[str, Any] = {
    "messages": [{"role": "user", "content": "hello"}],
}

_CHAT_CONTEXT_MODULE = "fleet_rlm.api.runtime_services.chat_context"


def live_chat_execution_context_type() -> type:
    """Return ``ChatExecutionContext`` from the live ``sys.modules`` entry.

    Use in ``isinstance`` checks instead of a module-level import when earlier
    tests may have reloaded ``chat_context`` and left stale class references.
    """
    mod = importlib.import_module(_CHAT_CONTEXT_MODULE)
    return mod.ChatExecutionContext


# ---------------------------------------------------------------------------
# RuntimeEvent builders
# ---------------------------------------------------------------------------


def make_started_event(payload: dict[str, Any] | None = None) -> RuntimeEvent:
    return RuntimeEvent(
        kind=RuntimeEventKind.TURN_STARTED,
        text="started",
        payload=payload
        or {
            "message_id": "msg-1",
            "selected_skills": ["skill-1"],
            "available_tools": ["repl_execute"],
            "execution_mode": "auto",
            "session_id": "sess-1",
            "run_id": "run-1",
        },
    )


def make_text_event(text: str) -> RuntimeEvent:
    return RuntimeEvent(kind=RuntimeEventKind.TEXT, text=text)


def make_done_event(payload: dict[str, Any] | None = None) -> RuntimeEvent:
    return RuntimeEvent(
        kind=RuntimeEventKind.DONE,
        text="done",
        payload=payload or {"history_turns": 1},
    )


def make_error_event(text: str = "boom") -> RuntimeEvent:
    return RuntimeEvent(kind=RuntimeEventKind.ERROR, text=text)


# ---------------------------------------------------------------------------
# Fake agent / agent-context (used via build_chat_agent_context stub)
# ---------------------------------------------------------------------------


class FakeChatAgent:
    """Minimal agent used to keep /api/chat tests off Daytona/LLM runtime."""

    def __init__(self) -> None:
        self.execution_mode: str | None = None

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        yield make_started_event()
        yield make_text_event(f"agent saw {kwargs.get('message', '')}")
        yield make_done_event()


class FakeChatAgentContext:
    def __init__(self, agent: FakeChatAgent) -> None:
        self.agent = agent
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeChatAgent:
        self.entered = True
        return self.agent

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> bool:
        self.exited = True
        return False


# ---------------------------------------------------------------------------
# SSE response parsing / assertions
# ---------------------------------------------------------------------------


def parse_sse_body(body: str) -> list[dict[str, Any] | str]:
    """Parse SSE ``data:`` lines into a list of JSON payloads.

    Returns dicts for JSON lines; returns the string ``[DONE]`` for the
    terminal marker.
    """
    parts: list[dict[str, Any] | str] = []
    for line in body.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line == "data: [DONE]":
            parts.append("[DONE]")
        elif line.startswith("data: "):
            payload_str = line[len("data: ") :]
            try:
                parts.append(json.loads(payload_str))
            except json.JSONDecodeError:
                parts.append(payload_str)
    return parts


def assert_sse_ok(response: Any) -> None:
    """Assert a successful SSE response."""
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:200]}"
    content_type = response.headers.get("content-type", "")
    assert "text/event-stream" in content_type, f"Expected text/event-stream, got {content_type}"


# ---------------------------------------------------------------------------
# stream_turn / identity stub factories
# ---------------------------------------------------------------------------


def stub_identity_dependency(identity: NormalizedIdentity):
    """Return a callable that returns *identity* (for dependency overrides)."""
    return lambda: identity


def stub_stream_turn(events: list[RuntimeEvent]):
    """Return a callable *stream_turn* stub yielding the given *events*."""

    async def _stub(*, ctx: ChatExecutionContext, agent_runtime: object, message: str) -> AsyncIterator[RuntimeEvent]:
        for ev in events:
            yield ev

    return _stub


# ---------------------------------------------------------------------------
# Autouse-fixture installers
# ---------------------------------------------------------------------------


def install_chat_agent_context_stub(monkeypatch: pytest.MonkeyPatch) -> list[FakeChatAgentContext]:
    """Stub build_chat_agent_context to return a tracked FakeChatAgentContext."""
    contexts: list[FakeChatAgentContext] = []
    chat_module = importlib.import_module("fleet_rlm.api.routers.chat")

    async def _build_context(runtime: object, pool: Any = None) -> FakeChatAgentContext:
        _ = runtime
        context = FakeChatAgentContext(FakeChatAgent())
        contexts.append(context)
        return context

    monkeypatch.setattr(chat_module, "build_chat_agent_context", _build_context)
    return contexts


def install_prepare_chat_runtime_stub(monkeypatch: pytest.MonkeyPatch) -> list[PreparedChatRuntime]:
    """Stub prepare_chat_runtime to return a tracked minimal PreparedChatRuntime."""
    chat_module = importlib.import_module("fleet_rlm.api.routers.chat")
    runtimes: list[PreparedChatRuntime] = []

    async def _prepare_runtime(**kwargs: Any) -> PreparedChatRuntime:
        config_deps = kwargs["config_deps"]
        persistence_deps = kwargs["persistence_deps"]
        runtime = PreparedChatRuntime(
            cfg=config_deps.config,
            planner_lm=object(),
            delegate_lm=None,
            repository=None,
            persistence=persistence_deps.local_store,
            persistence_required=False,
            identity_rows=None,
        )
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(chat_module, "prepare_chat_runtime", _prepare_runtime)
    return runtimes


# ---------------------------------------------------------------------------
# Default TestClient builder, shared by the chat_client (test_chat_sse.py)
# and chat_sse_client (test_cross_flows.py) fixtures, which differ only in
# name.
# ---------------------------------------------------------------------------


def build_default_chat_client(
    no_db_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    stub_identity: NormalizedIdentity,
) -> Iterator[TestClient]:
    """TestClient for the chat SSE endpoint with stubbed identity + stream_turn.

    Overrides:
    - ``require_http_identity`` -> returns a fixed ``NormalizedIdentity``
    - ``stream_turn`` in the chat router module -> yields controllable events
    """
    no_db_app.dependency_overrides[require_http_identity] = stub_identity_dependency(stub_identity)

    chat_module = importlib.import_module("fleet_rlm.api.routers.chat")
    monkeypatch.setattr(
        chat_module,
        "stream_turn",
        stub_stream_turn(
            [
                make_started_event(),
                make_text_event("Hello from stub!"),
                make_done_event(),
            ]
        ),
    )

    with TestClient(no_db_app) as client:
        yield client
