"""The Daytona broker is the only live Python namespace for a Turn."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, _SyncBridgeLoop, sandbox_backend


class _LocalBroker:
    """Exercise the backend protocol with JSON transport and a persistent namespace."""

    instances: ClassVar[list[_LocalBroker]] = []

    def __init__(self, _sandbox: Any, *, port: int, **_kwargs: Any) -> None:
        assert port > 0
        self.namespace: dict[str, Any] = {}
        self.calls: list[str] = []
        self.closed = False
        self.instances.append(self)

    def bind_tools(self, tools: dict[str, Any]) -> None:
        self.tools = tools

    def bind_async_bridge(self, _bridge: Any) -> None:
        pass

    def setup_source(self, source: str) -> str:
        return source

    def execute(self, code: str, variables: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
        assert timeout_s > 0
        self.calls.append(code)
        self.namespace.update(json.loads(json.dumps(variables, ensure_ascii=False, allow_nan=False)))
        stdout = io.StringIO()
        error = None
        final = None
        try:
            with contextlib.redirect_stdout(stdout):
                exec(compile(code, "<broker-test>", "exec"), self.namespace, self.namespace)
        except BaseException as exc:
            if type(exc).__name__ == "FleetFinalOutputError":
                final = exc.value
            else:
                error = str(exc)
        return {"stdout": stdout.getvalue(), "stderr": "", "error": error, "final": final}

    def stop(self, *, strict: bool) -> None:
        assert strict
        self.closed = True


def test_broker_namespace_persists_within_turn_and_resets_for_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    root = DaytonaCodeInterpreter(backend=sandbox_backend(MagicMock()))
    first = root.new_invocation()
    first.execute(
        "cached = session_context['workspace']['available']", {"session_context": {"workspace": {"available": True}}}
    )
    assert first.execute("print(cached)") == "True\n"
    assert len(_LocalBroker.instances) == 1

    second = root.new_invocation()
    with pytest.raises(Exception, match="cached"):
        second.execute("print(cached)")
    assert len(_LocalBroker.instances) == 2
    first.shutdown()
    second.shutdown()
    assert all(broker.closed for broker in _LocalBroker.instances)


def test_broker_receives_typed_submit_and_rejects_unserializable_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    backend = sandbox_backend(MagicMock())
    backend.ensure_submit([{"name": "answer", "type": "str", "required": True}])
    result = backend.run("SUBMIT(answer=note)", {"note": "café"})
    assert result.final == {"answer": "café"}
    assert "SUBMIT(answer=note)" in _LocalBroker.instances[0].calls[0]
    with pytest.raises(DaytonaAdapterError, match="binding 'opaque' contains unsupported type"):
        backend.run("pass", {"opaque": object()})
    backend.close()


def test_broker_submit_size_failure_is_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    _LocalBroker.instances.clear()
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.DaytonaHttpToolBroker", _LocalBroker)
    backend = sandbox_backend(MagicMock())
    backend.ensure_submit([{"name": "answer", "type": "str", "required": True}], max_output_chars=20)

    oversized = backend.run("SUBMIT(answer='x' * 30)")
    assert oversized.final is None
    assert "SUBMIT output is too large" in str(oversized.error)
    assert backend.run("SUBMIT(answer='short')").final == {"answer": "short"}
    backend.close()


@pytest.mark.asyncio
async def test_broker_resolves_awaitable_tools_and_returns_structured_failure() -> None:
    """Broker polling keeps host-tool failures structured."""

    class _Response:
        def __init__(self, body: dict[str, object]) -> None:
            self._body = body
            self.status_code = 200

        def json(self) -> dict[str, object]:
            return self._body

    class _Client:
        def __init__(self) -> None:
            self.posts: list[dict[str, object]] = []
            self._requests = [
                {"id": "ok-1", "lease": "lease-1", "tool_name": "async_tool", "args": [], "kwargs": {}},
                {"id": "bad-1", "lease": "lease-2", "tool_name": "missing", "args": [], "kwargs": {}},
            ]

        def get(self, _path: str) -> _Response:
            return _Response({"requests": self._requests})

        def post(self, _path: str, *, content: bytes, headers: dict[str, str]) -> _Response:
            assert headers == {"Content-Type": "application/json"}
            self.posts.append(json.loads(content))
            return _Response({"status": "ok"})

    async def async_tool() -> dict[str, object]:
        return {"ok": True}

    broker = DaytonaHttpToolBroker(MagicMock(), port=8765)
    client = _Client()
    broker._client = client  # type: ignore[assignment]
    broker.bind_tools({"async_tool": async_tool})
    broker.bind_async_bridge(_SyncBridgeLoop(caller_loop=asyncio.get_running_loop()))

    await asyncio.to_thread(broker._poll_once)

    assert client.posts[0]["result"] == {"ok": True}
    failure = client.posts[1]["tool_error"]
    assert isinstance(failure, dict)
    assert failure["category"] == "KeyError"
    assert failure["call_id"] == "bad-1"
