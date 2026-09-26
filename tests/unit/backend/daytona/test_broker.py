"""Focused tests for the sandbox-local broker protocol."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from fleet_rlm.daytona import broker as broker_module
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def embedded_server() -> Iterator[tuple[str, dict[str, str]]]:
    port = _free_port()
    secret = "test-broker-secret"
    source = (
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
                if httpx.get(f"{base_url}/health", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.02)
        else:
            pytest.fail("embedded broker did not start")
        yield base_url, headers
    finally:
        process.terminate()
        process.wait(timeout=2)


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


def test_embedded_server_preserves_failure_and_rejects_completed_duplicate(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    base_url, headers = embedded_server
    broker = DaytonaHttpToolBroker(object(), port=int(base_url.rsplit(":", 1)[1]))
    broker._secret = headers["X-Broker-Secret"]
    broker.bind_tools({"failing_tool": lambda: None})
    source = broker.setup_source("failing_tool()")
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        thread, responses = _run_execute(client, source)
        for _ in range(50):
            pending = client.get("/pending").json()["requests"]
            if pending:
                break
            time.sleep(0.01)
        assert pending
        request = pending[0]
        failure = {"category": "ProviderError", "message": "safe provider failure", "call_id": request["id"]}
        assert (
            client.post(
                "/result", json={"id": request["id"], "lease": request["lease"], "tool_error": failure}
            ).status_code
            == 200
        )
        thread.join(timeout=2)
        assert responses and responses[0].json()["tool_error"] == failure
        duplicate = client.post(
            "/tool_call",
            json={
                "id": request["id"],
                "tool_name": "failing_tool",
                "args": [],
                "kwargs": {},
            },
        )
        assert duplicate.status_code == 409


def test_embedded_server_bounds_output_before_response(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    base_url, headers = embedded_server
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        response = client.post(
            "/execute",
            json={"code": "print('x' * 100_000)", "variables": {}, "timeout_s": 2},
        )
    assert response.status_code == 200
    stdout = response.json()["stdout"]
    assert len(stdout) <= broker_module._MAX_OUTPUT_CHARS + len("\n...[sandbox output truncated]")
    assert "sandbox output truncated" in stdout


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


def test_retained_backend_resets_invocation_but_preserves_state_between_actions(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    from fleet_rlm.daytona.interpreter import sandbox_backend

    base_url, headers = embedded_server
    broker = DaytonaHttpToolBroker(object(), port=int(base_url.rsplit(":", 1)[1]))
    broker._secret = headers["X-Broker-Secret"]
    broker._url = base_url
    backend = sandbox_backend(object())
    backend._broker = broker
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        broker._client = client
        backend.bind_async_bridge(None)
        backend.bind_host_tools({"tool": lambda value: value + 1})
        assert backend.run("value = 40").error is None
        assert backend.run("value = tool(value)").error is None
        assert backend.run("print(value + 1)").stdout == "42\n"
        old_secret = broker._secret
        backend.bind_async_bridge(None)
        backend.bind_host_tools({"tool": lambda value: value + 10})
        assert backend.run("print('value' in globals()); print(tool(1))").stdout == "False\n11\n"
        assert client.post("/execute", headers={"X-Broker-Secret": old_secret}, json={}).status_code == 401


def test_settled_broker_rejects_new_calls_and_unknown_results(
    embedded_server: tuple[str, dict[str, str]],
) -> None:
    base_url, headers = embedded_server
    with httpx.Client(base_url=base_url, headers=headers, timeout=2) as client:
        assert client.post("/tool_call", json={"id": "late", "tool_name": "tool"}).status_code == 409
        assert client.post("/result", json={"id": "unknown", "lease": "old", "result": 1}).status_code == 404


def test_poll_records_rejected_tool_result_delivery() -> None:
    broker = DaytonaHttpToolBroker(object(), port=1)
    client = MagicMock()
    client.get.return_value.json.return_value = {
        "requests": [{"id": "call-1", "lease": "lease-1", "tool_name": "answer", "args": [], "kwargs": {}}]
    }
    client.post.return_value.status_code = 409
    broker._client = client
    broker.bind_tools({"answer": lambda: {"ok": True}})

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


@pytest.mark.parametrize("text", ("line\n" * 100, "\\" * 100, '"' * 100, "\u0001" * 100, "é🚀" * 100))
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
