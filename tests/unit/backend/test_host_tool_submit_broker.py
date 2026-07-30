"""B1: host-tool / SUBMIT broker binding completeness (offline seams)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from fleet_rlm.rlm.dspy_interpreter_contract import FinalOutput


class _RecordingTool:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, name: str) -> str:
        self.calls.append(((name,), {}))
        return f"loaded:{name}"


def test_submit_returns_final_output() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute('SUBMIT(answer="done")')

    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "done"}


def test_sandbox_code_invokes_host_mediated_tool() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    tool = _RecordingTool()
    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"load_skill": tool})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute("SUBMIT(answer=load_skill(name='demo'))")

    assert tool.calls == [(("demo",), {})]
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "loaded:demo"}


def test_llm_query_host_callable_is_reachable_from_sandbox_code() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    seen: list[str] = []

    def llm_query(prompt: str) -> str:
        seen.append(prompt)
        return f"sub:{prompt}"

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.tools.update({"llm_query": llm_query})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute("SUBMIT(answer=llm_query('ping'))")

    assert seen == ["ping"]
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "sub:ping"}


def test_shutdown_stops_broker_and_rejects_further_execute() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()
    assert isinstance(interp.execute('SUBMIT(answer="ok")'), FinalOutput)
    interp.shutdown()

    with pytest.raises(DaytonaAdapterError):
        interp.execute('SUBMIT(answer="again")')
    assert backend.closed is True


def test_host_tool_public_errors_are_sanitized() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    def leaky_tool() -> str:
        raise RuntimeError("failed api_key=sk-secret path=/tmp/secret")

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"leaky": leaky_tool})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute("leaky()")

    message = str(result)
    assert message.startswith("[Error]")
    assert "sk-secret" not in message
    assert "/tmp/secret" not in message


def test_http_broker_wrapper_source_and_fulfill_sanitize() -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_secret = "secret"
    broker._stopped = False

    source = broker._tool_wrapper_source("load_skill", lambda name: name)
    assert "def load_skill(name)" in source
    assert "X-Broker-Secret" in source

    posted: list[dict[str, object]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={})

    broker._client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://example.test")
    broker._fulfill(
        {
            "id": "c1",
            "lease_token": "tok",
            "tool_name": "load_skill",
            "args": [],
            "kwargs": {},
        },
        tool_executor=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("api_key=sk-secret path=/tmp/x")),
    )

    assert posted
    assert "sk-secret" not in str(posted[0].get("error", ""))
    assert "/tmp/x" not in str(posted[0].get("error", ""))


def test_http_broker_uses_isolated_port_for_server_and_wrappers() -> None:
    import hashlib

    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Process:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def code_run(self, _code: str) -> object:
            return type("Response", (), {"result": hashlib.sha256(self.sandbox.uploaded_content).hexdigest()})()

        def create_session(self, session_id: str) -> None:
            self.session_id = session_id

        def execute_session_command(self, session_id: str, _request: object) -> None:
            assert session_id == self.session_id

    class _Fs:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def upload_file(self, content: bytes, _path: str) -> None:
            self.sandbox.uploaded_content = content

    class _Sandbox:
        def __init__(self) -> None:
            self.uploaded_content = b""
            self.process = _Process(self)
            self.fs = _Fs(self)

        def get_preview_link(self, port: int) -> object:
            assert port == 3001
            return type("Preview", (), {"url": "http://preview.test", "token": "preview-token"})()

    sandbox = _Sandbox()
    broker = DaytonaHttpToolBroker(sandbox=sandbox, broker_port=3001)
    broker._wait_health = lambda **_kwargs: None  # type: ignore[method-assign]
    broker.ensure_started()
    source = broker._tool_wrapper_source("load_skill", lambda name: name)

    assert "localhost:3001/tool_call" in source
    assert broker._broker_port == 3001
    assert '("0.0.0.0", 3001)' in sandbox.uploaded_content.decode()


def test_http_broker_health_fails_fast_on_http_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_token = "preview-tok"
    broker._broker_secret = "secret"

    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        return httpx.Response(401, json={"error": "Unauthorized"})

    broker._client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://example.test")
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(DaytonaAdapterError, match="401") as exc_info:
        broker._wait_health(timeout_s=60.0)

    assert attempts["n"] == 1
    assert exc_info.value.cause_type == "BrokerHealthAuthError"


def test_http_broker_preview_headers_use_preview_token_only() -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_secret = "broker-secret"
    broker._broker_token = "preview-token"

    assert broker._preview_headers() == {
        "X-Broker-Secret": "broker-secret",
        "X-Daytona-Preview-Token": "preview-token",
    }


def test_http_broker_health_401_notes_missing_preview_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_token = ""
    broker._broker_secret = "secret"

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": "Unauthorized"})

    broker._client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://example.test")
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(DaytonaAdapterError, match="preview token missing") as exc_info:
        broker._wait_health(timeout_s=60.0)

    assert exc_info.value.cause_type == "BrokerHealthAuthError"


def test_http_broker_retries_transient_preview_link_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.errors import ProviderRequestError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Process:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def code_run(self, _code: str) -> object:
            import hashlib

            content = self.sandbox.uploaded_content
            return type("Response", (), {"result": hashlib.sha256(content).hexdigest()})()

        def create_session(self, session_id: str) -> None:
            self.session_id = session_id

        def execute_session_command(self, session_id: str, request: object) -> None:
            assert session_id == self.session_id
            del request

        def delete_session(self, session_id: str) -> None:
            self.deleted_session_id = session_id

    class _Fs:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def upload_file(self, content: bytes, path: str) -> None:
            del path
            self.sandbox.uploaded_content = content

    class _Sandbox:
        def __init__(self) -> None:
            self.uploaded_content = b""
            self.process = _Process(self)
            self.fs = _Fs(self)
            self.preview_attempts = 0

        def get_preview_link(self, port: int) -> object:
            assert port == 3000
            self.preview_attempts += 1
            if self.preview_attempts < 3:
                raise ProviderRequestError("502 Bad Gateway", cause_type="DaytonaError")
            return type("Preview", (), {"url": "http://preview.test", "token": "preview-token"})()

    sandbox = _Sandbox()
    broker = DaytonaHttpToolBroker(sandbox=sandbox)
    monkeypatch.setattr(broker, "_wait_health", lambda **_kwargs: None)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    broker.ensure_started()

    assert sandbox.preview_attempts == 3
    assert sleeps == [0.25, 0.5]
    assert broker._broker_url == "http://preview.test"
    assert broker._broker_token == "preview-token"


def test_http_broker_preview_link_failure_is_bounded_and_cleanup_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona.errors import ProviderRequestError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Process:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def code_run(self, _code: str) -> object:
            import hashlib

            content = self.sandbox.uploaded_content
            return type("Response", (), {"result": hashlib.sha256(content).hexdigest()})()

        def create_session(self, session_id: str) -> None:
            self.session_id = session_id

        def execute_session_command(self, session_id: str, request: object) -> None:
            assert session_id == self.session_id
            del request

        def delete_session(self, session_id: str) -> None:
            self.deleted_session_id = session_id

    class _Fs:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def upload_file(self, content: bytes, path: str) -> None:
            del path
            self.sandbox.uploaded_content = content

    class _Sandbox:
        def __init__(self) -> None:
            self.uploaded_content = b""
            self.process = _Process(self)
            self.fs = _Fs(self)
            self.preview_attempts = 0

        def get_preview_link(self, port: int) -> object:
            assert port == 3000
            self.preview_attempts += 1
            raise ProviderRequestError(
                "502 Bad Gateway api_key=sk-secret path=/tmp/private",
                cause_type="DaytonaError",
            )

    sandbox = _Sandbox()
    broker = DaytonaHttpToolBroker(sandbox=sandbox)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    with pytest.raises(ProviderRequestError, match=r"preview link.*3 attempts") as exc_info:
        broker.ensure_started()

    assert sandbox.preview_attempts == 3
    assert sleeps == [0.25, 0.5]
    assert "sk-secret" not in str(exc_info.value)
    assert "/tmp/private" not in str(exc_info.value)
    assert broker._broker_session_id == sandbox.process.session_id

    broker.stop()

    assert sandbox.process.deleted_session_id == sandbox.process.session_id


def test_http_broker_does_not_retry_preview_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.errors import ProviderRequestError
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Process:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def code_run(self, _code: str) -> object:
            import hashlib

            content = self.sandbox.uploaded_content
            return type("Response", (), {"result": hashlib.sha256(content).hexdigest()})()

        def create_session(self, _session_id: str) -> None:
            pass

        def execute_session_command(self, _session_id: str, _request: object) -> None:
            pass

    class _Fs:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox

        def upload_file(self, content: bytes, path: str) -> None:
            del path
            self.sandbox.uploaded_content = content

    class _Sandbox:
        def __init__(self) -> None:
            self.uploaded_content = b""
            self.process = _Process(self)
            self.fs = _Fs(self)
            self.preview_attempts = 0

        def get_preview_link(self, _port: int) -> object:
            self.preview_attempts += 1
            raise ProviderRequestError("Unauthorized", cause_type="AuthError", status_code=401)

    sandbox = _Sandbox()
    broker = DaytonaHttpToolBroker(sandbox=sandbox)
    monkeypatch.setattr(time, "sleep", lambda _delay: pytest.fail("auth failure was retried"))

    with pytest.raises(ProviderRequestError, match="preview link"):
        broker.ensure_started()

    assert sandbox.preview_attempts == 1


def test_http_broker_poll_and_fulfill_route_through_pooled_client() -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_token = "preview-tok"
    broker._broker_secret = "secret"

    seen_paths: list[str] = []
    pending_batches: list[list[dict[str, object]]] = [
        [{"id": "c1", "lease_token": "tok", "tool_name": "echo", "args": ["hi"], "kwargs": {}}]
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert request.headers["X-Daytona-Preview-Token"] == "preview-tok"
        assert request.headers["X-Broker-Secret"] == "secret"
        if request.url.path == "/pending":
            batch = pending_batches.pop(0) if pending_batches else []
            return httpx.Response(200, json={"requests": batch})
        assert request.url.path == "/result"
        return httpx.Response(200, json={})

    client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )
    broker._client = client

    fulfilled: list[str] = []
    assert broker._poll_once(lambda name, _args, _kwargs: fulfilled.append(name)) is True
    assert broker._poll_once(lambda name, _args, _kwargs: fulfilled.append(name)) is False

    assert fulfilled == ["echo"]
    assert broker._poll_count == 2
    assert broker._fulfilled_count == 1
    assert seen_paths == ["/pending", "/result", "/pending"]
    assert broker._http() is client


def test_execute_with_callbacks_records_per_execution_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_token = "tok"
    broker._broker_secret = "secret"

    pending_batches: list[list[dict[str, object]]] = [
        [{"id": "c1", "lease_token": "t", "tool_name": "echo", "args": [], "kwargs": {}}]
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pending":
            batch = pending_batches.pop(0) if pending_batches else []
            return httpx.Response(200, json={"requests": batch})
        return httpx.Response(200, json={})

    broker._client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    result = broker.execute_with_callbacks(
        run_code=lambda: "hello",
        tool_executor=lambda _name, _args, _kwargs: "ok",
    )

    assert result.stdout == "hello"
    stats = broker.last_execution_stats
    assert stats["tool_call_count"] == 1
    assert stats["poll_count"] >= 1


def test_stop_closes_pooled_client() -> None:
    from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker

    class _Process:
        def delete_session(self, _session_id: str) -> None:
            return None

    class _Sandbox:
        process = _Process()

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_secret = "secret"
    broker._client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
        base_url="http://example.test",
    )

    broker.stop()

    assert broker._client is None
