"""B1: host-tool / SUBMIT broker binding completeness (offline seams)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest

from fleet_rlm.rlm._dspy_compat import FinalOutput


class _RecordingTool:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, name: str) -> str:
        self.calls.append(((name,), {}))
        return f"loaded:{name}"


def test_co_located_worker_preserves_state_and_services_callbacks(tmp_path: Path) -> None:
    """
    Verify that a co-located broker preserves interpreter state and services callbacks across executions.

    Parameters:
        tmp_path (Path): Temporary directory used to store the generated broker server.
    """
    from fleet_rlm.daytona.broker import (
        _MAX_EXECUTE_OUTPUT_CHARS,
        _MAX_EXECUTE_REQUEST_BYTES,
        BROKER_SERVER_CODE,
        DaytonaHttpToolBroker,
    )

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = int(reservation.getsockname()[1])
    secret = "test-broker-secret"
    source = (
        BROKER_SERVER_CODE.replace("__BROKER_SECRET__", repr(secret))
        .replace("__BROKER_PORT__", str(port))
        .replace("__MAX_REQUEST_BYTES__", str(_MAX_EXECUTE_REQUEST_BYTES))
        .replace("__MAX_OUTPUT_CHARS__", str(_MAX_EXECUTE_OUTPUT_CHARS))
        .replace("__CONTEXT_MOUNT_ROOT__", "None")
        .replace("__CONTEXT_MANIFEST_SHA256__", "None")
    )
    server_path = tmp_path / "broker.py"
    server_path.write_text(source, encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    broker = DaytonaHttpToolBroker(sandbox=object(), broker_port=port, poll_interval_s=0.005)
    broker._broker_url = f"http://127.0.0.1:{port}"
    broker._broker_secret = secret
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                if broker._http().get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                pytest.fail("co-located worker did not become healthy")
            time.sleep(0.01)

        def llm_query_batched(prompts: list[str]) -> list[str]:
            return [f"sub:{prompt}" for prompt in prompts]

        broker.register_tools({"llm_query_batched": llm_query_batched})
        broker.execute_code(broker.submit_setup_code([{"name": "answer", "type": "str"}]))
        streamed: list[str] = []
        output = broker.execute_code(
            'import time\nprint("one", flush=True)\ntime.sleep(0.05)\nprint("two", flush=True)',
            on_stdout=streamed.append,
        )
        assert output.stdout == "one\ntwo\n"
        assert "one" in "".join(streamed)
        assert "two" in "".join(streamed)
        streamed_stats = dict(broker.last_execution_stats)
        assert streamed_stats["output_wait_requested_ms"] >= 0
        assert streamed_stats["output_wait_elapsed_ms"] >= 0
        assert streamed_stats["output_poll_count"] <= 8

        first = broker.execute_with_callbacks(
            run_code=lambda: broker.execute_code("value = 41"),
            tool_executor=lambda name, args, kwargs: (
                llm_query_batched(*args, **kwargs) if name == "llm_query_batched" else None
            ),
        )
        second = broker.execute_with_callbacks(
            run_code=lambda: broker.execute_code(
                "parts = llm_query_batched(['a', 'b'])\n"
                "SUBMIT(answer=f'{value + 1}:{parts[0]}:{parts[1]}:__FLEET_FINAL_OUTPUT__')"
            ),
            tool_executor=lambda name, args, kwargs: (
                llm_query_batched(*args, **kwargs) if name == "llm_query_batched" else None
            ),
        )

        assert first.error is None
        assert second.final == {"answer": "42:sub:a:sub:b:__FLEET_FINAL_OUTPUT__"}
        assert broker.last_execution_stats["tool_call_count"] == 1
    finally:
        if broker._client is not None:
            broker._client.close()
        process.terminate()
        process.wait(timeout=5)


def test_submit_returns_final_output() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    result = interp.execute('SUBMIT(answer="done")')

    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "done"}


def test_typed_submit_rejects_non_string_values_with_serialization_guidance() -> None:
    from dspy.primitives.code_interpreter import CodeExecutionError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    with pytest.raises(CodeExecutionError, match=r"json\.dumps") as caught:
        interp.execute("SUBMIT(answer={'section': 1})")

    assert not isinstance(caught.value, FinalOutput)
    result = interp.execute("import json\nSUBMIT(answer=json.dumps({'section': 1}, indent=2))")
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": '{\n  "section": 1\n}'}


def test_two_interpreters_do_not_share_python_variables() -> None:
    from dspy.primitives.code_interpreter import CodeExecutionError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    first = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    second = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first.output_fields = [{"name": "answer", "type": "str"}]
    second.output_fields = [{"name": "answer", "type": "str"}]
    first.start()
    second.start()

    first.execute("private_value = 41")

    with pytest.raises(CodeExecutionError, match=r"private_value.*not defined"):
        second.execute("SUBMIT(answer=str(private_value))")
    result = first.execute("SUBMIT(answer=str(private_value + 1))")
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "42"}


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
    from dspy.primitives.code_interpreter import CodeExecutionError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    def leaky_tool() -> str:
        raise RuntimeError("failed api_key=sk-secret path=/tmp/secret")

    backend = InProcessInterpreterBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"leaky": leaky_tool})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.start()

    with pytest.raises(CodeExecutionError) as caught:
        interp.execute("leaky()")

    message = str(caught.value)
    assert "sk-secret" not in message
    assert "/tmp/secret" not in message


def test_http_broker_wrapper_source_and_fulfill_sanitize() -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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

    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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
    broker = DaytonaHttpToolBroker(
        sandbox=sandbox,
        broker_port=3001,
        context_mount_root="/home/daytona/run",
        context_manifest_sha256="a" * 64,
    )
    broker._wait_health = lambda **_kwargs: None  # type: ignore[method-assign]
    broker.ensure_started()
    source = broker._tool_wrapper_source("load_skill", lambda name: name)

    assert "localhost:3001/tool_call" in source
    assert broker._broker_port == 3001
    assert '("0.0.0.0", 3001)' in sandbox.uploaded_content.decode()
    uploaded = sandbox.uploaded_content.decode()
    assert "def _fleet_load_context_manifest(raw_manifest):" in uploaded
    assert "_CONTEXT_MOUNT_ROOT = '/home/daytona/run'" in uploaded
    assert f"_CONTEXT_MANIFEST_SHA256 = {'a' * 64!r}" in uploaded


def test_http_broker_health_fails_fast_on_http_401(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
    from fleet_rlm.daytona.errors import DaytonaAdapterError

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
    from fleet_rlm.daytona.errors import DaytonaAdapterError

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
    from fleet_rlm.daytona.errors import ProviderRequestError

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
    from fleet_rlm.daytona.errors import ProviderRequestError

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
    from fleet_rlm.daytona.errors import ProviderRequestError

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
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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
    first_poll = broker._poll_once(lambda name, _args, _kwargs: fulfilled.append(name))
    second_poll = broker._poll_once(lambda name, _args, _kwargs: fulfilled.append(name))
    assert first_poll is True
    assert second_poll is False

    assert fulfilled == ["echo"]
    assert broker._poll_count == 2
    assert broker._fulfilled_count == 1
    assert seen_paths == ["/pending", "/result", "/pending"]
    assert broker._http() is client


def test_http_broker_copies_context_independently_into_parallel_fulfillment() -> None:
    from contextvars import ContextVar

    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox())
    broker._broker_url = "http://example.test"
    broker._broker_secret = "secret"
    pending = [
        {"id": "c1", "lease_token": "t1", "tool_name": "one", "args": [], "kwargs": {}},
        {"id": "c2", "lease_token": "t2", "tool_name": "two", "args": [], "kwargs": {}},
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        """
        Handle HTTP requests by returning and clearing pending requests for the pending endpoint.

        Parameters:
                request (httpx.Request): The incoming HTTP request.

        Returns:
                httpx.Response: A response containing pending requests for the `/pending` path,
                    or an empty JSON object for other paths.
        """
        nonlocal pending
        if request.url.path == "/pending":
            result, pending = pending, []
            return httpx.Response(200, json={"requests": result})
        return httpx.Response(200, json={})

    broker._client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )
    active_trace = ContextVar("active_trace", default="missing")
    active_trace.set("fleet-turn")
    observed: dict[str, tuple[str, str]] = {}

    def _execute(name: str, _args: list[object], _kwargs: dict[str, object]) -> str:
        """
        Record the inherited and active trace values for a tool invocation.

        Parameters:
            name (str): Name associated with the invocation.
            _args (list[object]): Positional arguments supplied to the invocation.
            _kwargs (dict[str, object]): Keyword arguments supplied to the invocation.

        Returns:
            str: The invocation name.
        """
        inherited = active_trace.get()
        active_trace.set(name)
        observed[name] = (inherited, active_trace.get())
        return name

    assert broker._poll_once(_execute) is True
    assert observed == {
        "one": ("fleet-turn", "one"),
        "two": ("fleet-turn", "two"),
    }
    assert active_trace.get() == "fleet-turn"


def test_execute_with_callbacks_records_per_execution_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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


def test_execute_with_callbacks_reuses_executor_and_reports_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox(), poll_interval_s=0.001)
    broker._broker_url = "http://example.test"
    broker._broker_token = "tok"
    broker._broker_secret = "secret"
    pending_batches: list[list[dict[str, object]]] = [
        [{"id": "c1", "lease_token": "t1", "tool_name": "echo", "args": [], "kwargs": {}}],
        [{"id": "c2", "lease_token": "t2", "tool_name": "echo", "args": [], "kwargs": {}}],
    ]

    monkeypatch.setattr(time, "sleep", lambda _delay: None)

    execution_index = 0
    completion_events = [Event(), Event()]

    def _run_code() -> str:
        nonlocal execution_index
        execution_index += 1
        assert completion_events[execution_index - 1].wait(timeout=1)
        return "hello"

    def _tool_executor(_name: str, _args: list[Any], _kwargs: dict[str, Any]) -> str:
        completion_events[execution_index - 1].set()
        return "ok"

    def _handler(request: httpx.Request) -> httpx.Response:
        """Handle pending-request polling and result-submission requests.

        Parameters:
            request (httpx.Request): The incoming HTTP request.

        Returns:
            httpx.Response: A response containing the next pending batch for
                ``/pending`` or an empty JSON object for ``/result``.
        """
        if request.url.path == "/pending":
            batch = (
                pending_batches[execution_index - 1].pop(0)
                if execution_index and pending_batches[execution_index - 1]
                else []
            )
            return httpx.Response(200, json={"requests": [batch] if batch else []})
        assert request.url.path == "/result"
        return httpx.Response(200, json={})

    broker._client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )

    snapshots: list[dict[str, int]] = []
    for _ in range(2):
        assert (
            broker.execute_with_callbacks(
                run_code=_run_code,
                tool_executor=_tool_executor,
            ).stdout
            == "hello"
        )
        snapshots.append(dict(broker.last_execution_stats))

    first, second = snapshots
    assert first["callback_executor_created"] == 1
    assert first["callback_executor_reused"] == 0
    assert second["callback_executor_created"] == 0
    assert second["callback_executor_reused"] == 1
    assert first["pending_batch_count"] == 1
    assert second["pending_request_count"] == 1
    for key in (
        "poll_latency_ms",
        "callback_dispatch_ms",
        "tool_execution_ms",
        "result_post_ms",
        "execution_wall_ms",
    ):
        assert key in second
        assert second[key] >= 0

    executor = broker._callback_executor
    assert executor is not None
    broker.stop()
    assert executor._shutdown is True


def test_execute_with_callbacks_polls_immediately_after_work_and_backs_off_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox(), poll_interval_s=0.01)
    broker._broker_url = "http://example.test"
    broker._broker_token = "tok"
    broker._broker_secret = "secret"
    pending = [{"id": "c1", "lease_token": "t1", "tool_name": "echo", "args": [], "kwargs": {}}]
    release = Event()
    events: list[str] = []
    empty_poll_count = 0
    pending_waits: list[str] = []
    sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        """Handle pending-request polling and result submissions in the test transport."""
        nonlocal empty_poll_count
        if request.url.path == "/pending":
            pending_waits.append(request.url.params.get("wait", "0"))
            if pending:
                events.append("pending_work")
                return httpx.Response(200, json={"requests": [pending.pop()]})
            events.append("pending_empty")
            empty_poll_count += 1
            if empty_poll_count >= 2:
                release.set()
            wait_value = request.url.params.get("wait", "0")
            waited_ms = max(1, int(float(wait_value) * 1_000)) if float(wait_value) > 0 else 0
            return httpx.Response(200, json={"requests": [], "waited_ms": waited_ms})
        events.append("result")
        return httpx.Response(200, json={})

    broker._client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )
    monkeypatch.setattr(time, "sleep", sleeps.append)

    result = broker.execute_with_callbacks(
        run_code=lambda: (release.wait(timeout=1) or True) and "done",
        tool_executor=lambda _name, _args, _kwargs: "ok",
    )

    assert result.stdout == "done"
    assert events.index("pending_work") < events.index("result") < events.index("pending_empty")
    assert any(float(value) > 0 for value in pending_waits)
    assert any(delay > 0 for delay in sleeps)
    assert broker.last_execution_stats["empty_poll_count"] >= 1
    assert broker.last_execution_stats["pending_wait_requested_ms"] > 0
    assert broker.last_execution_stats["pending_wait_elapsed_ms"] > 0


def test_poll_backoff_stays_finite_and_capped_for_large_empty_poll_counts() -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

    broker = DaytonaHttpToolBroker(sandbox=object(), poll_interval_s=1.0)

    assert broker._poll_backoff_delay(10**100) == pytest.approx(0.25)
    infinite_interval = DaytonaHttpToolBroker(sandbox=object(), poll_interval_s=float("inf"))
    assert infinite_interval._poll_backoff_delay(1) == pytest.approx(0.25)
    assert DaytonaHttpToolBroker(sandbox=object(), poll_interval_s=float("nan"))._poll_backoff_delay(1) == 0.0


def test_execute_code_attempts_final_output_release_after_poll_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

    class _Sandbox:
        pass

    release_flags: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/execute":
            return httpx.Response(
                200,
                json={"stdout": "", "stderr": "", "final": None, "error": None},
            )
        if request.url.path == "/output":
            release = request.url.params.get("release", "0")
            release_flags.append(release)
            if release == "1":
                return httpx.Response(
                    200,
                    json={"stdout": "", "stderr": "", "done": True, "next_offset": 0},
                )
            return httpx.Response(503)
        return httpx.Response(404)

    broker = DaytonaHttpToolBroker(sandbox=_Sandbox(), poll_interval_s=0.0)
    broker._broker_url = "http://example.test"
    broker._broker_token = "tok"
    broker._broker_secret = "secret"
    broker._client = httpx.Client(
        transport=httpx.MockTransport(_handler),
        base_url="http://example.test",
        headers=broker._preview_headers(),
    )
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    broker.execute_code("print('ok')", on_stdout=lambda _value: None)

    assert release_flags.count("0") <= 1
    assert release_flags[-1] == "1"


def test_stop_closes_pooled_client() -> None:
    from fleet_rlm.daytona.broker import DaytonaHttpToolBroker

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
