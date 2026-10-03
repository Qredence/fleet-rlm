"""Focused tests for the sandbox-local broker protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from fleet_rlm.daytona import broker as broker_module
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.observability.diagnostics import trace_failure_category


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def _start_embedded_server(prefix: str = "") -> Iterator[tuple[str, dict[str, str]]]:
    port = _free_port()
    secret = "test-broker-secret"
    source = prefix + (
        broker_module._SERVER_SOURCE.replace("__SECRET__", repr(secret))
        .replace("__PORT__", str(port))
        .replace("__MAX_REQUEST_BYTES__", str(broker_module._MAX_REQUEST_BYTES))
        .replace("__MAX_OUTPUT_CHARS__", str(broker_module._MAX_OUTPUT_CHARS))
        .replace("__DEFAULT_TOOL_TIMEOUT_S__", str(broker_module._DEFAULT_TOOL_TIMEOUT_S))
    )
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[4],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    headers = {"X-Broker-Secret": secret}
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{base_url}/health", headers=headers, timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.02)
        else:
            pytest.fail("embedded broker did not start")
        yield base_url, headers
    finally:
        process.terminate()
        process.wait(timeout=2)


@pytest.fixture
def embedded_server() -> Iterator[tuple[str, dict[str, str]]]:
    with _start_embedded_server() as server:
        yield server


def _run_execute(
    client: httpx.Client, code: str, *, timeout_s: float = 2
) -> tuple[threading.Thread, list[httpx.Response]]:
    responses: list[httpx.Response] = []
    thread = threading.Thread(
        target=lambda: responses.append(
            client.post("/execute", json={"code": code, "variables": {}, "timeout_s": timeout_s}, timeout=3)
        ),
        daemon=True,
    )
    thread.start()
    return thread, responses


@pytest.mark.parametrize(
    ("failure", "cause_type", "category"),
    [
        (httpx.ReadTimeout("private endpoint detail"), "BrokerExecutionTimeout", "timeout"),
        (httpx.ConnectError("private endpoint detail"), "BrokerExecutionError", "unknown"),
    ],
)
def test_execute_classifies_request_timeout_without_exposing_transport_detail(
    failure: Exception, cause_type: str, category: str
) -> None:
    broker = DaytonaHttpToolBroker(object(), port=1)
    client = MagicMock()
    client.post.side_effect = failure
    broker._client = client
    broker._url = "http://sandbox.invalid"
    broker._poll_once = lambda: None  # type: ignore[method-assign]

    with pytest.raises(DaytonaAdapterError) as caught:
        broker.execute("print('hello')", {}, timeout_s=2)

    assert caught.value.cause_type == cause_type
    assert trace_failure_category(caught.value) == category
    assert "private endpoint detail" not in str(caught.value)


def test_execute_transport_timeout_includes_response_grace() -> None:
    broker = DaytonaHttpToolBroker(object(), port=1)
    client = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"stdout": "", "error": None}
    client.post.return_value = response
    broker._client = client
    broker._url = "http://sandbox.invalid"
    broker._poll_once = lambda: None  # type: ignore[method-assign]

    broker.execute("pass", {}, timeout_s=7)

    kwargs = client.post.call_args.kwargs
    assert kwargs["timeout"] == 7 + broker_module._EXECUTE_RESPONSE_GRACE_S
    assert kwargs["json"]["timeout_s"] == 7


@pytest.mark.parametrize("post_expiry_delay_s", [0.0, 0.5])
def test_execute_returns_sandbox_timeout_when_host_tool_outlives_the_action(
    post_expiry_delay_s: float, caplog: pytest.LogCaptureFixture
) -> None:
    """The live late-delivery race, unpatched: a host tool runs past the
    action deadline. The sandbox times its wait out and replies with the
    tool error, the late result is dropped as benign, and execute() returns
    that recoverable outcome instead of a transport timeout."""
    with _start_embedded_server() as (base_url, headers):
        port = int(base_url.rsplit(":", 1)[1])

        def answer() -> dict[str, bool]:
            time.sleep(3)
            return {"ok": True}

        broker = DaytonaHttpToolBroker(object(), port=port)
        broker._secret = headers["X-Broker-Secret"]
        broker.bind_tools({"answer": answer})
        broker._url = base_url
        source = broker.setup_source(
            "import time as _t\n"
            "try:\n"
            "    print(answer())\n"
            "except Exception:\n"
            f"    _t.sleep({post_expiry_delay_s})\n"
            "    raise\n"
        )
        with httpx.Client(base_url=base_url, headers=headers, timeout=5) as client:
            broker._client = client
            started = time.monotonic()
            result = broker.execute(source, {}, timeout_s=2)
            elapsed = time.monotonic() - started

    assert result["error_category"] == "HTTPError"
    assert broker._delivery_error is None
    assert "category=duplicate_call" in caplog.text
    assert elapsed < 2 + broker_module._EXECUTE_RESPONSE_GRACE_S


def test_execute_exposes_the_action_deadline_to_dispatched_host_tools(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    from fleet_rlm.rlm.budget import current_host_action_deadline

    base_url, headers = embedded_server
    seen: list[float | None] = []

    def answer() -> dict[str, bool]:
        seen.append(current_host_action_deadline())
        return {"ok": True}

    broker = DaytonaHttpToolBroker(object(), port=int(base_url.rsplit(":", 1)[1]))
    broker._secret = headers["X-Broker-Secret"]
    broker.bind_tools({"answer": answer})
    broker._url = base_url
    with httpx.Client(base_url=base_url, headers=headers, timeout=5) as client:
        broker._client = client
        before = time.monotonic()
        result = broker.execute(broker.setup_source("print(answer())"), {}, timeout_s=4)
        after = time.monotonic()

    assert "ok" in str(result.get("stdout"))
    assert len(seen) == 1 and seen[0] is not None
    assert before + 4 <= seen[0] <= after + 4
    assert current_host_action_deadline() is None


def test_tool_call_uses_execution_deadline(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    base_url, headers = embedded_server
    broker = DaytonaHttpToolBroker(object(), port=int(base_url.rsplit(":", 1)[1]))
    broker._secret = headers["X-Broker-Secret"]
    broker.bind_tools({"waiting_tool": lambda: None})
    source = broker.setup_source("waiting_tool()")
    started = time.monotonic()
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        thread, responses = _run_execute(client, source, timeout_s=0.2)
        for _ in range(50):
            if client.get("/pending").json()["requests"]:
                break
            time.sleep(0.01)
        thread.join(timeout=2)
    assert responses and responses[0].status_code == 200
    assert time.monotonic() - started < 1


def test_health_probe_requires_the_invocation_secret(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    """A surviving broker must not be able to answer the next invocation's startup probe.

    `_ensure_started` treats a 200 from `/health` as proof that *its own* server came up.
    If health were unauthenticated, a stale server still holding the previous Turn's
    secret would satisfy that probe while the new server died on the already-bound port;
    the host would then talk to the stale namespace and every later call would 401 for
    the rest of the Sandbox's life.
    """
    base_url, headers = embedded_server
    assert httpx.get(f"{base_url}/health", headers=headers, timeout=2).status_code == 200
    assert httpx.get(f"{base_url}/health", headers={"X-Broker-Secret": "x" * 32}, timeout=2).status_code == 401
    assert httpx.get(f"{base_url}/health", timeout=2).status_code == 401


def test_broker_persists_state_between_actions_and_rejects_rebinding(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    from fleet_rlm.daytona.interpreter import sandbox_backend

    base_url, headers = embedded_server
    broker = DaytonaHttpToolBroker(object(), port=int(base_url.rsplit(":", 1)[1]))
    broker._secret = headers["X-Broker-Secret"]
    backend = sandbox_backend(object())
    backend.bind_async_bridge(None)
    backend.bind_host_tools({"tool": lambda value: value + 1})
    broker.bind_tools(backend._bound_tools)
    broker._url = base_url
    backend._broker = broker
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        broker._client = client
        assert backend.run("value = 40").error is None
        assert backend.run("value = tool(value)").error is None
        assert backend.run("print(value + 1)").stdout == "42\n"
        with pytest.raises(DaytonaAdapterError, match="tool bindings changed"):
            backend.bind_host_tools({"tool": lambda value: value + 10})
        with pytest.raises(DaytonaAdapterError, match="tool outcomes changed"):
            backend.bind_tool_outcomes(tool_settled=None, tool_failed=None)
        with pytest.raises(DaytonaAdapterError, match="async bridge changed"):
            backend.bind_async_bridge(None)
        with pytest.raises(DaytonaAdapterError, match="output contract changed"):
            backend.ensure_submit([{"name": "answer"}], 100)
        assert backend._output_fields is None
        assert client.post("/reset", json={}).status_code == 404


def test_settled_broker_rejects_new_calls_and_unknown_results(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    base_url, headers = embedded_server
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        assert client.post("/tool_call", json={"id": "late", "tool_name": "tool"}).status_code == 409
        assert client.post("/result", json={"id": "unknown", "lease": "old", "result": 1}).status_code == 404


def _poll_with_rejected_result(status_code: int, body: object | None) -> tuple[DaytonaHttpToolBroker, MagicMock]:
    broker = DaytonaHttpToolBroker(object(), port=1)
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [{"id": "call-1", "lease": "lease-1", "tool_name": "answer", "args": [], "kwargs": {}}]
    }
    if body is None:
        # MagicMock().text is not parseable JSON — the unparseable-body shape.
        client.post.return_value.status_code = status_code
    else:
        client.post.return_value = MagicMock(status_code=status_code, text=json.dumps(body))
    broker._client = client
    broker.bind_tools({"answer": lambda: {"ok": True}})
    return broker, client


def test_poll_treats_late_duplicate_call_delivery_as_benign(caplog: pytest.LogCaptureFixture) -> None:
    broker, _ = _poll_with_rejected_result(409, {"error": "duplicate call"})

    broker._poll_once()

    assert broker._delivery_error is None
    assert "delivered after sandbox abandonment" in caplog.text
    assert "category=duplicate_call" in caplog.text


def test_poll_treats_stale_lease_delivery_as_benign(caplog: pytest.LogCaptureFixture) -> None:
    broker, _ = _poll_with_rejected_result(409, {"error": "stale lease"})

    broker._poll_once()

    assert broker._delivery_error is None
    assert "category=stale_lease" in caplog.text


def test_poll_records_duplicate_result_delivery_as_fatal() -> None:
    broker, _ = _poll_with_rejected_result(409, {"error": "duplicate result"})

    broker._poll_once()

    assert broker._delivery_error is not None
    assert broker._delivery_error.cause_type == "BrokerDeliveryError"


def test_poll_records_rejected_tool_result_delivery_with_unparseable_body() -> None:
    broker, _ = _poll_with_rejected_result(409, None)

    broker._poll_once()

    assert broker._delivery_error is not None
    assert broker._delivery_error.cause_type == "BrokerDeliveryError"


def test_poll_records_server_error_delivery_as_fatal() -> None:
    broker, _ = _poll_with_rejected_result(500, {"error": "boom"})

    broker._poll_once()

    assert broker._delivery_error is not None
    assert broker._delivery_error.cause_type == "BrokerDeliveryError"


@pytest.mark.parametrize("error_type", [RuntimeError, httpx.HTTPError])
def test_poll_continues_after_settlement_callback_failure(error_type, caplog: pytest.LogCaptureFixture) -> None:
    settled = MagicMock(side_effect=[error_type("private callback error"), None])
    broker = DaytonaHttpToolBroker(object(), port=1, tool_settled=settled)
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [
            {"id": f"call-{index}", "lease": f"lease-{index}", "tool_name": "answer", "kwargs": {"value": index}}
            for index in (1, 2)
        ]
    }
    client.post.return_value.status_code = 200
    broker._client = client
    broker.bind_tools({"answer": lambda value: value})

    broker._poll_once()

    assert [json.loads(call.kwargs["content"])["result"] for call in client.post.call_args_list] == [1, 2]
    assert settled.call_count == 2
    settled.assert_called_with("answer", {"value": 2}, 2)
    assert broker._delivery_error is not None
    assert broker._delivery_error.cause_type == "BrokerDeliveryError"
    assert "phase=settlement category=callback_error" in caplog.text
    assert "private callback error" not in caplog.text


def test_poll_delivers_async_host_tool_result_through_application_bridge() -> None:
    class Bridge:
        def run(self, awaitable, **_kwargs):
            return asyncio.run(awaitable)

    async def append_workspace_text(path: str, content: str) -> dict[str, object]:
        await asyncio.sleep(0)
        return {"ok": True, "path": path, "bytes": len(content)}

    broker = DaytonaHttpToolBroker(object(), port=1, async_bridge=Bridge())
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [
            {
                "id": "call-1",
                "lease": "lease-1",
                "tool_name": "append_workspace_text",
                "args": [],
                "kwargs": {"path": "notes/findings.md", "content": "durable"},
            }
        ]
    }
    client.post.return_value.status_code = 200
    broker._client = client
    broker.bind_tools({"append_workspace_text": append_workspace_text})

    broker._poll_once()

    assert broker._delivery_error is None
    assert json.loads(client.post.call_args.kwargs["content"]) == {
        "id": "call-1",
        "lease": "lease-1",
        "result": {"ok": True, "path": "notes/findings.md", "bytes": 7},
    }
    assert client.post.call_args.kwargs["headers"] == {"Content-Type": "application/json"}


@pytest.mark.parametrize("text", ["line\n" * 100])
def test_result_envelope_uses_compact_utf8_json_for_escaped_text(text: str) -> None:
    payload = broker_module._encode_result_envelope({"id": "call", "lease": "lease", "result": {"text": text}})

    assert payload == json.dumps(
        {"id": "call", "lease": "lease", "result": {"text": text}},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def test_result_envelope_accepts_the_exact_limit_and_rejects_one_byte_more() -> None:
    empty = {"id": "call", "lease": "lease", "result": ""}
    overhead = len(broker_module._encode_result_envelope(empty))
    at_limit = {**empty, "result": "x" * (broker_module._MAX_REQUEST_BYTES - overhead)}

    assert len(broker_module._encode_result_envelope(at_limit)) == broker_module._MAX_REQUEST_BYTES
    with pytest.raises(ValueError, match="request limit"):
        broker_module._encode_result_envelope({**at_limit, "result": at_limit["result"] + "x"})


def test_poll_returns_a_typed_error_when_a_tool_result_exceeds_the_envelope() -> None:
    failed: list[tuple[str, dict[str, object]]] = []
    broker = DaytonaHttpToolBroker(
        object(),
        port=1,
        tool_failed=lambda name, args: failed.append((name, dict(args))),
    )
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [{"id": "call-1", "lease": "lease-1", "tool_name": "answer", "args": [], "kwargs": {}}]
    }
    client.post.return_value.status_code = 200
    broker._client = client
    broker.bind_tools({"answer": lambda: "x" * broker_module._MAX_REQUEST_BYTES})

    broker._poll_once()

    assert failed == [("answer", {})]
    assert json.loads(client.post.call_args.kwargs["content"])["tool_error"]["category"] == "ToolResultTooLarge"


def test_broker_runtime_allows_bounded_high_precision_integer_conversion() -> None:
    from fleet_rlm.daytona.broker import _SERVER_SOURCE

    assert "sys.set_int_max_str_digits(200_000)" in _SERVER_SOURCE


@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
def test_stop_retains_session_after_nonstrict_delete_failure_for_retry(error_type: type[Exception]) -> None:
    class Process:
        calls = 0

        def delete_session(self, session: str) -> None:
            assert session == "fleet-tool-broker-test"
            self.calls += 1
            if self.calls == 1:
                raise error_type("delete failed")

    process = Process()
    broker = DaytonaHttpToolBroker(SimpleNamespace(process=process), port=1)
    client = MagicMock()
    broker._client = client
    broker._session = "fleet-tool-broker-test"

    broker.stop()

    assert broker._stopped is True
    assert broker._client is None
    assert broker._session == "fleet-tool-broker-test"
    client.close.assert_called_once_with()

    broker.stop()

    assert broker._session is None
    assert process.calls == 2
    client.close.assert_called_once_with()


def test_stop_retains_http_client_when_close_fails_and_surfaces_strict_error() -> None:
    class Process:
        def delete_session(self, _session: str) -> None:
            return None

    broker = DaytonaHttpToolBroker(SimpleNamespace(process=Process()), port=1)
    client = MagicMock()
    client.close.side_effect = [RuntimeError("close failed"), None]
    broker._client = client
    broker._session = "fleet-tool-broker-test"

    with pytest.raises(RuntimeError, match="close failed"):
        broker.stop(strict=True)

    assert broker._client is client
    assert broker._session is None
    broker.stop(strict=True)
    assert broker._client is None
    assert client.close.call_count == 2


def test_poll_settles_required_mutation_only_after_remote_acknowledgement() -> None:
    settled: list[tuple[str, dict[str, object], object]] = []
    broker = DaytonaHttpToolBroker(
        object(),
        port=1,
        tool_settled=lambda name, args, result: settled.append((name, dict(args), result)),
    )
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [
            {
                "id": "call-1",
                "lease": "lease-1",
                "tool_name": "append_workspace_text",
                "args": [],
                "kwargs": {"path": "notes/findings.md", "content": "durable"},
            }
        ]
    }
    client.post.return_value.status_code = 200
    broker._client = client
    broker.bind_tools({"append_workspace_text": lambda **_kwargs: {"ok": True}})

    broker._poll_once()

    assert settled == [("append_workspace_text", {"path": "notes/findings.md", "content": "durable"}, {"ok": True})]


def _held_result_server(gate: Path) -> contextlib.AbstractContextManager[tuple[str, dict[str, str]]]:
    """Hold each woken tool-call waiter until ``gate`` exists, widening the result-ready window.

    Only timed waits are held: the tool-call waiter is the server's sole timed
    wait, while ``threading.Thread.start`` waits without a timeout.
    """
    prefix = (
        "import os as _gate_os, threading as _gate_threading, time as _gate_time\n"
        "class _HeldEvent(_gate_threading.Event):\n"
        "    def wait(self, timeout=None):\n"
        "        woke = super().wait(timeout)\n"
        f"        while woke and timeout is not None and not _gate_os.path.exists({str(gate)!r}):\n"
        "            _gate_time.sleep(0.01)\n"
        "        return woke\n"
        "_gate_threading.Event = _HeldEvent\n"
    )
    return _start_embedded_server(prefix)


def _expired_wait_server(hold: Path) -> contextlib.AbstractContextManager[tuple[str, dict[str, str]]]:
    """Expire the first timed tool-call wait only once ``hold`` exists.

    The call stays pending while the waiter is held, so the host broker can
    pick it up; when ``hold`` is touched the wait expires exactly as a deadline
    hit would (504 + the call moved to _completed), making the host's later
    result delivery arrive late.
    """
    prefix = (
        "import os as _gate_os, threading as _gate_threading, time as _gate_time\n"
        "class _ExpireOnceEvent(_gate_threading.Event):\n"
        "    def wait(self, timeout=None):\n"
        "        if timeout is not None and not getattr(self, '_expired', False):\n"
        "            self._expired = True\n"
        f"            while not _gate_os.path.exists({str(hold)!r}):\n"
        "                _gate_time.sleep(0.01)\n"
        "            return False\n"
        "        return super().wait(timeout)\n"
        "_gate_threading.Event = _ExpireOnceEvent\n"
    )
    return _start_embedded_server(prefix)


def _pending_lease(client: httpx.Client) -> tuple[str, str | None]:
    for _ in range(200):
        requests = client.get("/pending").json()["requests"]
        if requests:
            return str(requests[0]["id"]), requests[0]["lease"]
        time.sleep(0.01)
    raise AssertionError("tool call never became pending")


def _tool_source(port: int, secret: str) -> str:
    broker = DaytonaHttpToolBroker(object(), port=port)
    broker._secret = secret
    broker.bind_tools({"answer": lambda: None})
    return broker.setup_source("print(answer())")


def test_result_is_accepted_once_before_the_waiter_consumes_it(tmp_path: Path) -> None:
    gate = tmp_path / "release"
    with (
        _held_result_server(gate) as (base_url, headers),
        httpx.Client(base_url=base_url, headers=headers, timeout=3) as client,
    ):
        source = _tool_source(int(base_url.rsplit(":", 1)[1]), headers["X-Broker-Secret"])
        thread, responses = _run_execute(client, source, timeout_s=5)
        call_id, lease = _pending_lease(client)

        first = client.post("/result", json={"id": call_id, "lease": lease, "result": "first"})
        duplicate = client.post("/result", json={"id": call_id, "lease": lease, "result": "second"})
        gate.touch()
        thread.join(timeout=5)

    assert (first.status_code, duplicate.status_code) == (200, 409)
    assert responses[0].json()["stdout"] == "first\n"


def test_result_requires_an_issued_lease(embedded_server: tuple[str, dict[str, str]]) -> None:
    base_url, headers = embedded_server
    with httpx.Client(base_url=base_url, headers=headers, timeout=3) as client:
        thread, _ = _run_execute(client, "import time; time.sleep(1)", timeout_s=5)
        call: list[httpx.Response] = []
        for _ in range(100):
            waiter = threading.Thread(
                target=lambda: call.append(client.post("/tool_call", json={"id": "call-1", "tool_name": "answer"})),
                daemon=True,
            )
            waiter.start()
            waiter.join(timeout=0.05)
            if waiter.is_alive():
                break
            call.clear()
            time.sleep(0.01)
        unleased = client.post("/result", json={"id": "call-1", "result": "forged"})
        assert unleased.status_code == 409
        call_id, lease = _pending_lease(client)
        stale = client.post("/result", json={"id": call_id, "lease": "stale", "result": "forged"})
        delivered = client.post("/result", json={"id": call_id, "lease": lease, "result": "ok"})
        waiter.join(timeout=5)
        consumed = client.post("/result", json={"id": call_id, "lease": lease, "result": "late"})
        thread.join(timeout=5)

    assert stale.status_code == 409
    assert delivered.status_code == 200
    assert consumed.status_code == 409
    assert call[0].json() == {"result": "ok"}


def test_execute_survives_late_result_delivery_after_wait_expiry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hold = tmp_path / "expire-now"
    with _expired_wait_server(hold) as (base_url, headers):
        port = int(base_url.rsplit(":", 1)[1])
        tool_started = threading.Event()
        tool_release = threading.Event()

        def answer() -> dict[str, bool]:
            tool_started.set()
            tool_release.wait(timeout=10)
            return {"ok": True}

        broker = DaytonaHttpToolBroker(object(), port=port)
        broker._secret = headers["X-Broker-Secret"]
        broker.bind_tools({"answer": answer})
        broker._url = base_url
        with httpx.Client(base_url=base_url, headers=headers, timeout=5) as client:
            broker._client = client
            source = _tool_source(port, headers["X-Broker-Secret"])
            outcomes: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def _run() -> None:
                try:
                    outcomes.append(broker.execute(source, {}, timeout_s=5))
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()

            # The broker's poll picks the pending call up and blocks in the tool.
            assert tool_started.wait(timeout=5)
            # Expire the sandbox-side wait: the call moves to _completed and the
            # action sees a 504, exactly as a deadline hit would. The hold-loop
            # polls every 10ms, so this bounded sleep guarantees the expiry is
            # processed before the late result is released.
            hold.touch()
            time.sleep(0.1)

            # The late host result now arrives for an already-completed call.
            tool_release.set()
            thread.join(timeout=10)

    assert not errors, f"execute raised: {errors!r}"
    assert outcomes, "execute never returned a response"
    assert outcomes[0]["error_category"] == "HTTPError"
    assert broker._delivery_error is None
    assert "delivered after sandbox abandonment" in caplog.text
    assert "category=duplicate_call" in caplog.text
